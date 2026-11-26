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
from app.domain import notifications, rbac, schedule, transfer_rules
from app.domain.contributions import matched_totals
from app.persistence.models import (
    Account,
    AppMeta,
    Budget,
    BudgetLineSide,
    BudgetPeriodType,
    BudgetStatus,
    ExpectedMovement,
    ExpectedSource,
    Frequency,
    NotificationEvent,
    NotificationKind,
    NotificationRule,
    NotificationState,
    Role,
    SharedContributionProposal,
    TransferRule,
    UserProfile,
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
    state: Literal["pending", "accepted", "kept", "expired"] = "pending"


def split_key(household_id: int, account_id: int) -> str:
    return f"shared_split:{household_id}:{account_id}"


def guided_pot_ids(session: Session, household_id: int) -> set[int]:
    accounts = set(session.scalars(select(Account.id).where(
        Account.household_id == household_id, Account.archived.is_(False),
        Account.owner_profile_id.is_(None),
    )))
    records = session.scalars(select(AppMeta).where(
        AppMeta.key.startswith(f"shared_split:{household_id}:"),
    ))
    return {pot_id for record in records
            if (pot_id := int(record.key.rsplit(":", 1)[1])) in accounts
            and SharedSplitPolicy.model_validate_json(record.value).mode != "amount"}


def read(
    session: Session, household_id: int, account_id: int,
) -> tuple[dict[str, SharedContributionProposal], list[ContributionProposal]]:
    # Older decisions remain durable. Only active scheduling windows are loaded.
    lower = date.today() - timedelta(days=40)
    rows = session.scalars(select(SharedContributionProposal).where(
        SharedContributionProposal.household_id == household_id,
        SharedContributionProposal.account_id == account_id,
        SharedContributionProposal.effective_date >= lower,
    ).order_by(SharedContributionProposal.effective_date, SharedContributionProposal.id)).all()
    return {row.id: row for row in rows}, [ContributionProposal.model_validate(row.data)
                                          for row in rows]


def save(
    session: Session, household_id: int, account_id: int,
    rows: dict[str, SharedContributionProposal], changes: list[ContributionProposal],
) -> None:
    proposed = {item.id: item for item in changes}
    for row_id, row in rows.items():
        if row_id not in proposed:
            session.delete(row)
    for item in changes:
        payload = item.model_dump(mode="json")
        if existing := rows.get(item.id):
            if existing.data != payload:
                existing.data = payload
        else:
            session.add(SharedContributionProposal(
                id=item.id, household_id=household_id, account_id=account_id,
                effective_date=item.effective_date, data=payload,
            ))
    session.flush()


def _notify(session: Session, household_id: int, change: ContributionProposal) -> None:
    recipient = change.approver_profile_id or change.profile_id
    _notify_recipient(session, household_id, recipient, f"shared-change:{change.id}", {
        **change.model_dump(mode="json"), "path": "/shared",
        "title": "Shared contribution updated" if change.state == "accepted"
        else "Shared contribution change awaiting approval",
    })


def _notify_recipient(
    session: Session, household_id: int, recipient: int, key: str, payload: dict[str, object],
) -> None:
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
        notifications.raise_event(session, rule, key, payload)


def close_notification(session: Session, proposal_id: str) -> None:
    events = session.scalars(select(NotificationEvent).where(
        NotificationEvent.dedup_key == f"shared-change:{proposal_id}",
    ))
    for event in events:
        event.state = NotificationState.acked


def expire(session: Session, household_id: int, today: date) -> None:
    rows = session.scalars(select(SharedContributionProposal).where(
        SharedContributionProposal.household_id == household_id,
        SharedContributionProposal.effective_date < today,
        SharedContributionProposal.data["state"].as_string() == "pending",
    )).all()
    for row in rows:
        row.data = {**row.data, "state": "expired"}
        close_notification(session, row.id)


def invalidate_for_rule(session: Session, rule: TransferRule) -> None:
    row, changes = read(session, rule.household_id, rule.dest_account_id)
    if not row:
        return
    expired = [proposal for proposal in changes
               if proposal.rule_id == rule.id and proposal.state == "pending"]
    if expired:
        for proposal in expired:
            close_notification(session, proposal.id)
        save(session, rule.household_id, rule.dest_account_id, row,
             [proposal for proposal in changes if proposal not in expired])


def next_unpaid_date(session: Session, rule: TransferRule, today: date) -> date | None:
    movements = list(session.scalars(select(ExpectedMovement).where(
        ExpectedMovement.household_id == rule.household_id,
        ExpectedMovement.source_type == ExpectedSource.transfer_rule,
        ExpectedMovement.source_id == rule.id,
        ExpectedMovement.expected_date >= today,
    )))
    totals = matched_totals(session, [movement.id for movement in movements])
    unavailable_days = {movement.expected_date for movement in movements
                        if movement.dismissed_at is not None or totals[movement.id] > 0}
    days = transfer_rules.occurrences(rule, today + timedelta(days=100), today, session=session)
    return next((day for day in days if day not in unavailable_days), None)


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


def eligible_approver(session: Session, household_id: int, nominated: int) -> int | None:
    profile = session.get(UserProfile, nominated)
    if (profile is not None and profile.household_id == household_id
            and rbac.can(profile, rbac.Action.write)):
        return profile.id
    return session.scalar(select(UserProfile.id).where(
        UserProfile.household_id == household_id, UserProfile.is_active.is_(True),
        UserProfile.role == Role.admin,
    ).order_by(UserProfile.id).limit(1))


def _is_monthly_fixed(rule: TransferRule) -> bool:
    return (rule.frequency == Frequency.monthly
            and json.loads(rule.amount_spec).get("kind") == "fixed")


def propose(
    session: Session, budget: Budget, reason: str, *, account_id: int | None = None,
) -> None:
    today = date.today()
    if (budget.owner_profile_id is not None or budget.end_date < today
            or budget.period_type != BudgetPeriodType.monthly
            or budget.status not in (BudgetStatus.approved, BudgetStatus.locked)):
        return
    session.flush()
    household_id = budget.household_id
    overlapping = set(session.scalars(select(Budget.id).where(
        Budget.household_id == household_id, Budget.owner_profile_id.is_(None),
        Budget.id != budget.id, Budget.period_type == BudgetPeriodType.monthly,
        Budget.start_date <= budget.end_date,
        Budget.end_date >= budget.start_date,
        Budget.status.in_((BudgetStatus.approved, BudgetStatus.locked)),
    )))
    target = sum(line.effective_pence for line in budget.lines
                 if line.side == BudgetLineSide.expense)
    guided = guided_pot_ids(session, household_id)
    settings = session.scalars(select(AppMeta).where(
        AppMeta.key.like(f"shared_split:{household_id}:%"),
    )).all()
    for setting in settings:
        pot_id = int(setting.key.rsplit(":", 1)[1])
        if account_id is not None and pot_id != account_id:
            continue
        pot = session.get(Account, pot_id)
        if pot is None or pot.archived or pot.household_id != household_id:
            continue
        policy = SharedSplitPolicy.model_validate_json(setting.value)
        by_profile: dict[int, TransferRule] = {}
        ambiguous = False
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
                ambiguous = True
                continue
            by_profile[owner] = rule
        if policy.mode != "amount":
            members = set(policy.member_ids or policy.percentages or policy.fixed_pence)
            if policy.remainder_profile_id is not None:
                members.add(policy.remainder_profile_id)
            membership_changed = bool(members and members != by_profile.keys())
            ambiguous = ambiguous or not by_profile or membership_changed
        dates = {owner: next_unpaid_date(session, rule, max(today, budget.start_date))
                 for owner, rule in by_profile.items()}
        current = {owner: transfer_rules.fixed_amount_at(
            rule, min(dates[owner] or budget.end_date, budget.end_date),
        ) for owner, rule in by_profile.items()}
        next_amounts = (_proposed_amounts(policy, current, target)
                        if current and not ambiguous else None)
        row, previous = read(session, household_id, pot_id)
        changes = [item for item in previous
                   if (item.budget_id != budget.id or item.state == "accepted")
                   and not (item.state == "pending" and item.budget_id in overlapping)]
        fixed_total = sum(policy.fixed_pence.values())
        conflict = (next_amounts is None and policy.mode == "fixed_remainder"
                    and policy.remainder_profile_id in current and fixed_total > target)
        same_period = {owner for owner, due in dates.items()
                       if due is not None and due <= budget.end_date}
        kept_rules = {(item.rule_id, item.old_pence, item.new_pence, item.effective_date)
                      for item in previous if item.budget_id == budget.id and item.state == "kept"}
        funded = (sum(
            amount if owner in same_period and (
                by_profile[owner].id, current[owner], amount, dates[owner],
            ) not in kept_rules else current[owner]
            for owner, amount in next_amounts.items()
        ) if next_amounts else target)
        recipient = (policy.approver_profile_id or policy.remainder_profile_id
                     or min(current, default=None))
        if ambiguous:
            recipient = session.scalar(select(UserProfile.id).where(
                UserProfile.household_id == household_id, UserProfile.is_active.is_(True),
                UserProfile.role == Role.admin,
            ).order_by(UserProfile.id).limit(1)) or recipient
        _reconcile_conflict(session, household_id, pot_id, budget.id, recipient,
                            target, fixed_total if conflict else None, funded,
                            overlap=bool(overlapping) and guided == {pot_id},
                            ambiguous=ambiguous and guided == {pot_id})
        if next_amounts and not overlapping and guided == {pot_id}:
            for owner, amount in next_amounts.items():
                if amount == current[owner]:
                    continue
                nominated = (policy.approver_profile_id if policy.approval == "delegated"
                             else None) or owner
                approver = eligible_approver(session, household_id, nominated)
                if approver is None:
                    continue
                delegated = approver if approver != owner else None
                rule = by_profile[owner]
                date_due = dates[owner]
                if date_due is None or date_due > budget.end_date:
                    continue
                kept = next((item for item in previous if item.state in ("pending", "kept") and (
                    item.budget_id, item.rule_id, item.old_pence, item.new_pence,
                    item.effective_date, item.approver_profile_id,
                ) == (budget.id, rule.id, current[owner], amount, date_due, delegated)), None)
                change = kept or ContributionProposal(
                    id=uuid4().hex, profile_id=owner, rule_id=rule.id, budget_id=budget.id,
                    approver_profile_id=delegated,
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


def _reconcile_conflict(
    session: Session, household_id: int, pot_id: int, budget_id: int, recipient: int | None,
    target: int, fixed_total: int | None, funded: int, *, overlap: bool, ambiguous: bool,
) -> None:
    prefix = f"shared-split-conflict:{pot_id}:{budget_id}:"
    conflict = ambiguous or overlap or fixed_total is not None or funded != target
    key = f"{prefix}{recipient}:{target}:{fixed_total}:{funded}:{overlap}:{ambiguous}:"
    for event in session.scalars(select(NotificationEvent).join(
        NotificationRule, NotificationRule.id == NotificationEvent.rule_id,
    ).where(
        NotificationRule.household_id == household_id,
        NotificationEvent.dedup_key.startswith(prefix),
        NotificationEvent.state != NotificationState.acked,
    )):
        if conflict and event.dedup_key.startswith(key):
            conflict = False
        else:
            event.state = NotificationState.acked
    if conflict and recipient is not None:
        _notify_recipient(session, household_id, recipient, key + uuid4().hex, {
            "path": "/shared", "title": "Shared contribution split needs updating",
            "budget_id": budget_id, "account_id": pot_id,
            "fixed_total_pence": fixed_total, "new_target_pence": target,
            "scheduled_total_pence": funded,
            "overlapping_budgets": overlap,
            "ambiguous_schedules": ambiguous,
        })


def reassess_pending(session: Session, household_id: int, account_id: int) -> None:
    if session.get(AppMeta, split_key(household_id, account_id)) is None:
        return
    rows, proposals = read(session, household_id, account_id)
    pending = [item for item in proposals if item.state == "pending"]
    if pending:
        for item in pending:
            close_notification(session, item.id)
        save(session, household_id, account_id, rows,
             [item for item in proposals if item.state != "pending"])
    for budget in session.scalars(select(Budget).where(
        Budget.household_id == household_id, Budget.owner_profile_id.is_(None),
        Budget.end_date >= date.today(), Budget.period_type == BudgetPeriodType.monthly,
        Budget.status.in_((BudgetStatus.approved, BudgetStatus.locked)),
    ).order_by(Budget.start_date, Budget.id)):
        propose(session, budget, "Shared contribution settings updated", account_id=account_id)


def apply(session: Session, rule: TransferRule, proposal: ContributionProposal) -> None:
    set_effective_amount(session, rule, proposal.effective_date, proposal.new_pence)


def set_effective_amount(session: Session, rule: TransferRule, on: date, pence: int) -> None:
    spec = json.loads(rule.amount_spec)
    changes = spec.get("effective_amounts", [])
    changes = [item for item in changes if item["date"] != on.isoformat()]
    changes.append({"date": on.isoformat(), "pence": pence})
    spec.setdefault("original_pence", spec["pence"])
    changes.sort(key=lambda item: item["date"])
    spec.update(pence=changes[-1]["pence"], effective_amounts=changes)
    rule.amount_spec = json.dumps(spec)
    schedule.rematerialize_transfer_rule(session, rule)
