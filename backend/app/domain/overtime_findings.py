"""Deterministic, snapshot-based payslip overtime matching."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

TOLERANCE_PENCE = 50


@dataclass(frozen=True)
class Candidate:
    id: int
    work_date: date
    hours: Decimal
    hourly_rate_pence: int
    multiplier_bps: int

    @property
    def group(self) -> tuple[int, int]:
        return self.hourly_rate_pence, self.multiplier_bps


@dataclass(frozen=True)
class PayslipLine:
    id: str
    hours: Decimal
    printed_rate_pence: int


@dataclass(frozen=True)
class Allocation:
    line_id: str
    entry_id: int
    hours: Decimal


@dataclass(frozen=True)
class Finding:
    line_id: str
    status: str
    printed_hours: Decimal
    allocated_hours: Decimal
    delta_hours: Decimal
    allocations: tuple[Allocation, ...]
    reason: str | None = None
    line_ids: tuple[str, ...] = ()
    logged_hours: Decimal = Decimal("0")
    employer_owed_pence: int = 0
    printed_rate_pence: int | None = None
    hourly_rate_pence: int | None = None
    multiplier_bps: int | None = None


def expected_rate(candidate: Candidate) -> int:
    return int((Decimal(candidate.hourly_rate_pence)
                * candidate.multiplier_bps / Decimal(10_000)).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP))


def _owed(hours: Decimal, rate: int) -> int:
    return int((hours.copy_abs() * rate).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def match_lines(
    lines: list[PayslipLine],
    candidates: list[Candidate],
    *,
    cutoff_start: date,
    cutoff_end: date,
) -> list[Finding]:
    """Match grouped payslip lines against immutable logged snapshots."""
    eligible = sorted(
        (candidate for candidate in candidates
         if cutoff_start <= candidate.work_date <= cutoff_end
         and candidate.hours >= 0),
        key=lambda candidate: (candidate.work_date, candidate.id))
    groups: dict[tuple[int, int], list[Candidate]] = {}
    for candidate in eligible:
        groups.setdefault(candidate.group, []).append(candidate)
    remaining = {candidate.id: candidate.hours for candidate in eligible}
    grouped_lines: dict[tuple[int, int], list[PayslipLine]] = {}
    output: list[Finding] = []
    for line in lines:
        matching = [
            group for group in groups
            if abs(expected_rate(Candidate(
                -1, cutoff_start, Decimal("0"), group[0], group[1]))
            - line.printed_rate_pence) <= TOLERANCE_PENCE
        ]
        if len(matching) > 1:
            output.append(Finding(
                line_id=line.id, line_ids=(line.id,), status="ambiguous",
                printed_hours=line.hours, allocated_hours=Decimal("0"),
                delta_hours=line.hours, allocations=(),
                printed_rate_pence=line.printed_rate_pence,
                reason="multiple exact rate groups are within the 50p tolerance"))
        elif not matching:
            output.append(Finding(
                line_id=line.id, line_ids=(line.id,),
                status="rate-discrepancy-incomplete-history",
                printed_hours=line.hours, allocated_hours=Decimal("0"),
                delta_hours=line.hours, allocations=(),
                printed_rate_pence=line.printed_rate_pence,
                reason="no configured rate group is within the 50p tolerance"))
        else:
            grouped_lines.setdefault(matching[0], []).append(line)

    for group in groups:
        grouped_lines.setdefault(group, [])
    for group, group_lines in grouped_lines.items():
        group_candidates = groups[group]
        logged_total = sum(candidate.hours for candidate in group_candidates)
        if not group_lines:
            for candidate in group_candidates:
                if candidate.hours > 0:
                    output.append(Finding(
                        line_id=f"omitted:{candidate.id}", status="omitted",
                        printed_hours=Decimal("0"),
                        allocated_hours=Decimal("0"),
                        delta_hours=-candidate.hours, allocations=(),
                        logged_hours=candidate.hours,
                        hourly_rate_pence=group[0],
                        multiplier_bps=group[1],
                        reason="logged overtime has no payslip line"))
            continue
        printed_total = sum(line.hours for line in group_lines)
        group_delta = printed_total - logged_total
        allocations_by_line: dict[str, list[Allocation]] = {
            line.id: [] for line in group_lines}
        for line in group_lines:
            left = line.hours
            for candidate in group_candidates:
                amount = min(left, remaining[candidate.id])
                if amount <= 0:
                    continue
                remaining[candidate.id] -= amount
                left -= amount
                allocations_by_line[line.id].append(
                    Allocation(line.id, candidate.id, amount))
                if left == 0:
                    break
        rates: dict[int, list[PayslipLine]] = {}
        for line in group_lines:
            rates.setdefault(line.printed_rate_pence, []).append(line)
        rate_items = list(rates.items())
        unallocated_logged = max(Decimal("0"), -group_delta)
        unallocated_paid = max(Decimal("0"), Decimal(group_delta))
        for index, (printed_rate, rate_lines) in enumerate(rate_items):
            allocations = tuple(
                allocation for line in rate_lines
                for allocation in allocations_by_line[line.id])
            paid_hours = sum((line.hours for line in rate_lines), Decimal("0"))
            allocated = sum((a.hours for a in allocations), Decimal("0"))
            logged_hours = allocated
            # Any unpaid remainder belongs to the final printed rate group,
            # rather than becoming a second finding for the same missing pay.
            if index == len(rate_items) - 1:
                logged_hours += unallocated_logged
            delta: Decimal = paid_hours - logged_hours
            if delta > 0:
                delta = min(delta, unallocated_paid)
                unallocated_paid -= delta
            rate_status = "match"
            if delta < 0:
                rate_status = "underpaid"
            elif delta > 0:
                rate_status = "paid-extra"
            output.append(Finding(
                line_id=rate_lines[0].id,
                line_ids=tuple(item.id for item in rate_lines),
                status=rate_status, printed_hours=paid_hours,
                allocated_hours=allocated, delta_hours=delta,
                allocations=allocations,
                logged_hours=logged_hours,
                employer_owed_pence=_owed(delta, printed_rate)
                if delta < 0 else 0,
                printed_rate_pence=printed_rate, hourly_rate_pence=group[0],
                multiplier_bps=group[1]))
    return output
