from datetime import date
from decimal import Decimal

from app.domain.overtime_findings import (
    Candidate,
    PayslipLine,
    expected_rate,
    match_lines,
)


def candidate(entry_id: int, day: int, hours: str,
              rate: int = 1000, multiplier: int = 15_000) -> Candidate:
    return Candidate(entry_id, date(2025, 4, day), Decimal(hours), rate,
                     multiplier)


def test_exact_and_oldest_first_partial_allocations() -> None:
    findings = match_lines(
        [PayslipLine("a", Decimal("10"), 1500)],
        [candidate(2, 2, "4"), candidate(1, 1, "8")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert findings[0].status == "underpaid"
    assert [allocation.entry_id for allocation in findings[0].allocations] == [1, 2]
    assert findings[0].allocated_hours == Decimal("10")


def test_tolerance_boundary_and_ambiguity() -> None:
    exact = expected_rate(candidate(1, 1, "2"))
    boundary = match_lines(
        [PayslipLine("boundary", Decimal("2"), exact + 50)],
        [candidate(1, 1, "2")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert boundary[0].status == "match"
    ambiguous = match_lines(
        [PayslipLine("ambiguous", Decimal("2"), 1550)],
        [candidate(1, 1, "2", multiplier=15_000),
         candidate(2, 2, "2", multiplier=16_000)],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert ambiguous[0].status == "ambiguous"


def test_duplicate_printed_lines_share_group_total() -> None:
    findings = match_lines(
        [PayslipLine("first", Decimal("5"), 1500),
         PayslipLine("second", Decimal("5"), 1500)],
        [candidate(1, 1, "10")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == "match"
    assert finding.line_ids == ("first", "second")
    assert finding.printed_hours == Decimal("10")
    assert finding.logged_hours == Decimal("10")


def test_underpaid_is_one_finding_not_duplicated() -> None:
    findings = match_lines(
        [PayslipLine("a", Decimal("5"), 1500),
         PayslipLine("b", Decimal("5"), 1500)],
        [candidate(1, 1, "12")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert len(findings) == 1
    assert findings[0].status == "underpaid"
    assert findings[0].delta_hours == Decimal("-2")
    assert findings[0].employer_owed_pence == 3000


def test_paid_extra_is_one_finding_for_duplicate_lines() -> None:
    findings = match_lines(
        [PayslipLine("a", Decimal("5"), 1500),
         PayslipLine("b", Decimal("5"), 1500)],
        [candidate(1, 1, "8")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert [f.status for f in findings] == ["paid-extra"]
    assert findings[0].delta_hours == Decimal("2")


def test_logged_without_printed_line_is_omitted() -> None:
    findings = match_lines(
        [], [candidate(1, 1, "5")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert findings[0].status == "omitted"
    assert findings[0].delta_hours == Decimal("-5")


def test_underpaid_group_has_one_owed_finding() -> None:
    findings = match_lines(
        [PayslipLine("paid", Decimal("10"), 1500)],
        [candidate(1, 1, "12")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert len(findings) == 1
    assert findings[0].status == "underpaid"
    assert findings[0].delta_hours == Decimal("-2")
    assert findings[0].employer_owed_pence == 3000


def test_paid_extra_group_has_one_draft_delta() -> None:
    findings = match_lines(
        [PayslipLine("paid", Decimal("10"), 1500)],
        [candidate(1, 1, "8")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert len(findings) == 1
    assert findings[0].status == "paid-extra"
    assert findings[0].delta_hours == Decimal("2")


def test_zero_logged_known_rate_is_paid_extra_not_unknown() -> None:
    findings = match_lines(
        [PayslipLine("paid", Decimal("10"), 1500)],
        [candidate(-1001, 1, "0")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert findings[0].status == "paid-extra"


def test_same_rounded_rate_distinct_snapshot_groups_are_ambiguous() -> None:
    findings = match_lines(
        [PayslipLine("paid", Decimal("1"), 1500)],
        [candidate(1, 1, "1", rate=1000, multiplier=15000),
         candidate(2, 2, "1", rate=937, multiplier=16000)],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert findings[0].status == "ambiguous"


def test_after_cutoff_candidate_is_not_consumed() -> None:
    findings = match_lines(
        [PayslipLine("paid", Decimal("1"), 1500)],
        [candidate(1, 20, "1")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 10))
    assert findings[0].status == "rate-discrepancy-incomplete-history"


def test_oldest_partial_entry_is_consumed_before_newer_entry() -> None:
    findings = match_lines(
        [PayslipLine("paid", Decimal("3"), 1500)],
        [candidate(2, 2, "4"), candidate(1, 1, "2")],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert [a.entry_id for a in findings[0].allocations] == [1, 2]
    assert [a.hours for a in findings[0].allocations] == [
        Decimal("2"), Decimal("1")]


def test_empty_lines_with_two_groups_reports_both_omitted() -> None:
    findings = match_lines(
        [], [candidate(1, 1, "2"), candidate(2, 2, "3", rate=900)],
        cutoff_start=date(2025, 4, 1), cutoff_end=date(2025, 4, 30))
    assert [finding.status for finding in findings] == ["omitted", "omitted"]
