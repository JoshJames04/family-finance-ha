"""Proposals for changes to a household's agreed contribution schedule."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import SharedSplitPolicy
from app.domain import notifications, schedule, transfer_rules
from app.persistence.models import (
    Account,
    AppMeta,
    Budget,
    BudgetLineSide,
    BudgetStatus,
    ExpectedMatch,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    NotificationEvent,
    NotificationKind,
    NotificationRule,
    NotificationState,
    TransferRule,
)


class ContributionProposal(BaseModel):
    id: str
    profile_id: int
    approver_profile_id: int | None = None
    rule_id: int
    budget_id: int
    old_pence: int
    new_pence: int
    effective_date: date
    old_target_pence: int
    new_target_pence: int
    reason: str
    state: Literal["pending", "accepted", "kept"] = "pending"


def split_key(household_id: int, account_id: int) -> str:
    return f"shared_split:{household_id}:{account_id}"


def _proposal_key(household_id: int, account_id: int) -> str:
    return f"shared_proposal:{household_id}:{account_id}"


def read(
    session: Session, household_id: int, account_id: int,
) -> tuple[AppMeta | None, list[ContributionProposal]]:
    row = session.get(AppMeta, _proposal_key(household_id, account_id))
    return row, [ContributionProposal.model_validate(item)
                 for item in json.loads(row.value)] if row else []


def save(
    session: Session, household_id: int, account_id: int,
    row: AppMeta | None, changes: list[ContributionProposal],
) -> None:
    value = json.dumps([item.model_dump(mode="json") for item in changes])
    if row is None:
        session.add(AppMeta(key=_proposal_key(household_id, account_id), value=value))
    else:
        row.value = value


def _notify(session: Session, household_id: int, change: ContributionProposal) -> None:
    recipient = change.approver_profile_id or change.profile_id
    rule = session.scalar(select(NotificationRule).where(
        NotificationRule.household_id == household_id,
        NotificationRule.kind == NotificationKind.shared_contribution_change,
        NotificationRule.target_profile_id == recipient,
    ).order_by(NotificationRule.id))
    if rule is None:
        rule = notifications.create_rule(
            session, household_id, name="Shared contribution changes",
            kind=NotificationKind.shared_contribution_change, channel="in_app",
            target_profile_id=recipient,
        )
    if rule.enabled:
        notifications.raise_event(session, rule, f"shared-change:{change.id}", {
            **change.model_dump(mode="json"), "path": "/shared",
            "title": "Shared contribution updated" if change.state == "accepted"
            else "Shared contribution change awaiting approval",
        })


def close_notification(session: Session, proposal_id: str) -> None:
    events = session.scalars(select(NotificationEvent).where(
        NotificationEvent.dedup_key == f"shared-change:{proposal_id}",
    ))
    for event in events:
        event.state = NotificationState.acked


def next_unpaid_date(session: Session, rule: TransferRule, today: date) -> date | None:
    matched_days = set(session.scalars(select(ExpectedMovement.expected_date).join(
        ExpectedMatch, ExpectedMatch.movement_id == ExpectedMovement.id,
    ).where(
        ExpectedMovement.household_id == rule.household_id,
        ExpectedMovement.source_type == ExpectedSource.transfer_rule,
        ExpectedMovement.source_id == rule.id,
        ExpectedMovement.expected_date >= today,
    )))
    days = transfer_rules.occurrences(rule, today + timedelta(days=100), today, session=session)
    return next((day for day in days if day not in matched_days), None)


def _proposed_amounts(
    policy: SharedSplitPolicy, current: dict[int, int], target: int,
) -> dict[int, int] | None:
    if policy.mode == "fixed_remainder":
        remainder = policy.remainder_profile_id
        if remainder is None or set(policy.fixed_pence) | {remainder} != current.keys():
            return None
        amount = target - sum(policy.fixed_pence.values())
        return {**policy.fixed_pence, remainder: amount} if amount >= 0 else None
    if policy.mode == "amount":
        return None
    weights = policy.percentages if policy.mode == "percent" else dict.fromkeys(current, 1)
    if set(weights) != current.keys() or not (total := sum(weights.values())):
        return None
    amounts, cumulative, allocated = {}, 0, 0
    for member, weight in sorted(weights.items()):
        cumulative += weight
        next_amount = (2 * target * cumulative + total) // (2 * total)
        amounts[member] = next_amount - allocated
        allocated = next_amount
    return amounts


def _is_monthly_fixed(rule: TransferRule) -> bool:
    return (rule.frequency == Frequency.monthly
            and json.loads(rule.amount_spec).get("kind") == "fixed")


def propose(session: Session, budget: Budget, reason: str) -> None:
    today = date.today()
    if (budget.owner_profile_id is not None or budget.end_date < today
            or budget.status not in (BudgetStatus.approved, BudgetStatus.locked)):
        return
    session.flush()
    household_id = budget.household_id
    target = sum(line.effective_pence for line in budget.lines
                 if line.side == BudgetLineSide.expense)
    settings = session.scalars(select(AppMeta).where(
        AppMeta.key.like(f"shared_split:{household_id}:%"),
    )).all()
    for setting in settings:
        pot_id = int(setting.key.rsplit(":", 1)[1])
        pot = session.get(Account, pot_id)
        if pot is None or pot.archived or pot.household_id != household_id:
            continue
        policy = SharedSplitPolicy.model_validate_json(setting.value)
        by_profile: dict[int, TransferRule] = {}
        for rule, owner in session.execute(select(TransferRule, Account.owner_profile_id).join(
            Account, Account.id == TransferRule.source_account_id,
        ).where(
            TransferRule.household_id == household_id,
            TransferRule.dest_account_id == pot_id,
            TransferRule.active.is_(True),
            Account.archived.is_(False),
            Account.owner_profile_id.is_not(None),
        )):
            if owner is None or owner in by_profile or not _is_monthly_fixed(rule):
                by_profile.clear()
                break
            by_profile[owner] = rule
        dates = {owner: next_unpaid_date(session, rule, max(today, budget.start_date))
                 for owner, rule in by_profile.items()}
        current = {owner: transfer_rules.fixed_amount_at(rule, due)
                   for owner, rule in by_profile.items() if (due := dates[owner]) is not None}
        next_amounts = _proposed_amounts(policy, current, target) if current else None
        row, previous = read(session, household_id, pot_id)
        changes = [item for item in previous if item.budget_id != budget.id]
        if next_amounts:
            for owner, amount in next_amounts.items():
                if amount == current[owner]:
                    continue
                rule = by_profile[owner]
                date_due = dates[owner]
                assert date_due is not None
                kept = next((item for item in previous if (
                    item.budget_id, item.rule_id, item.old_pence, item.new_pence,
                    item.effective_date,
                ) == (budget.id, rule.id, current[owner], amount, date_due)), None)
                change = kept or ContributionProposal(
                    id=uuid4().hex, profile_id=owner, rule_id=rule.id, budget_id=budget.id,
                    approver_profile_id=policy.approver_profile_id if policy.approval == "delegated"
                    else None,
                    old_pence=current[owner], new_pence=amount, effective_date=date_due,
                    old_target_pence=sum(current.values()), new_target_pence=target, reason=reason,
                )
                if kept is None:
                    if policy.approval == "automatic":
                        apply(session, rule, change)
                        change.state = "accepted"
                    _notify(session, household_id, change)
                changes.append(change)
        changes.sort(key=lambda item: (item.effective_date, item.profile_id, item.budget_id))
        if changes != previous:
            active = {change.id for change in changes}
            for stale in previous:
                if stale.state == "pending" and stale.id not in active:
                    close_notification(session, stale.id)
            save(session, household_id, pot_id, row, changes)


def apply(session: Session, rule: TransferRule, proposal: ContributionProposal) -> None:
    spec = json.loads(rule.amount_spec)
    changes = spec.get("effective_amounts", [])
    changes = [item for item in changes if item["date"] != proposal.effective_date.isoformat()]
    changes.append({"date": proposal.effective_date.isoformat(), "pence": proposal.new_pence})
    spec.setdefault("original_pence", spec["pence"])
    changes.sort(key=lambda item: item["date"])
    spec.update(pence=changes[-1]["pence"], effective_amounts=changes)
    rule.amount_spec = json.dumps(spec)
    schedule.rematerialize_transfer_rule(session, rule)
