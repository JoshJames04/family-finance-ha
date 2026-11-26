"""Outstanding cash requirements for a shared bills account."""
from __future__ import annotations

import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BillsSpaceFunding, BillsSpaceMovement
from app.domain import transfer_rules
from app.domain.accounts import current_balance, current_movement_account
from app.domain.contributions import matched_totals
from app.persistence.models import (
    Account,
    AccountType,
    AuditEvent,
    Confidence,
    ExpectedMovement,
    ExpectedSource,
    TransferRule,
)


def suggest(
    session: Session, account: Account, today: date, through: date | None, buffer_pence: int,
) -> BillsSpaceFunding:
    horizon = today + timedelta(days=90)
    movements = list(session.scalars(select(ExpectedMovement).where(
        ExpectedMovement.household_id == account.household_id,
        ExpectedMovement.account_id == account.id,
        ExpectedMovement.dismissed_at.is_(None),
        ExpectedMovement.expected_date >= today,
        ExpectedMovement.expected_date <= (through or horizon),
        current_movement_account(),
    ).order_by(ExpectedMovement.expected_date, ExpectedMovement.amount_pence, ExpectedMovement.id)))
    totals = matched_totals(session, [movement.id for movement in movements])
    rule_ids = {movement.source_id for movement in movements
                if movement.source_type == ExpectedSource.transfer_rule}
    rules = {rule.id: rule for rule in session.scalars(
        select(TransferRule).where(TransferRule.id.in_(rule_ids))
    )} if rule_ids else {}
    source_ids = {rule.source_account_id for rule in rules.values()}
    joint_ids = set(session.scalars(select(Account.id).where(
        Account.id.in_(source_ids), Account.household_id == account.household_id,
        Account.type == AccountType.joint, Account.owner_profile_id.is_(None),
        Account.archived.is_(False),
    ))) if source_ids else set()
    standing_rules = {rid for rid, rule in rules.items()
                      if rule.active and rule.start_date != rule.end_date
                      and rule.source_account_id in joint_ids
                      and json.loads(rule.amount_spec).get("kind") == "fixed"}
    standing = next((movement for movement in movements
                     if movement.expected_date > today
                     and movement.amount_pence > totals[movement.id]
                     and _is_standing_top_up(standing_rules, movement)), None)
    next_top_up = standing.expected_date if standing and through is None else None
    through = through or next_top_up or today + timedelta(days=30)
    balance = current_balance(session, account.id)
    running = balance
    low = None
    first_shortfall = None
    rows = []
    # An explicitly accepted one-off is intended to fund that day's bills.
    # Other same-day income is assumed to arrive after the debits.
    def order(movement: ExpectedMovement) -> tuple[date, int, int]:
        rule = rules.get(movement.source_id)
        proactive = (movement.source_type == ExpectedSource.transfer_rule
                     and movement.amount_pence > 0 and rule is not None
                     and rule.start_date == rule.end_date)
        return movement.expected_date, 0 if proactive else 1, movement.amount_pence

    for movement in sorted(movements, key=order):
        if movement.expected_date > through:
            break
        outstanding = max(0, abs(movement.amount_pence) - totals[movement.id])
        if not outstanding:
            continue
        rule = rules.get(movement.source_id)
        if movement.amount_pence < 0 and movement.source_type in (
            ExpectedSource.commitment, ExpectedSource.subscription,
        ):
            outstanding = -outstanding
        elif not (movement.amount_pence > 0
                  and movement.source_type == ExpectedSource.transfer_rule
                  and rule is not None and rule.active
                  and movement.confidence == Confidence.scheduled
                  and not (movement.expected_date == next_top_up
                           and _is_standing_top_up(standing_rules, movement))):
            continue
        if movement.expected_date > today and low is None:
            low = running
            if running < buffer_pence:
                first_shortfall = today
        running += outstanding
        if outstanding < 0 or low is not None:
            low = running if low is None else min(low, running)
        if low is not None and first_shortfall is None and running < buffer_pence:
            first_shortfall = movement.expected_date
        rows.append(BillsSpaceMovement(
            date=movement.expected_date, label=movement.label, pence=outstanding,
        ))
    if low is None:
        low = running
        if running < buffer_pence:
            first_shortfall = today
    parent = transfer_rules.space_parent(session, account.id)
    editable = standing is not None and (
        parent is None or not derived_space_rule(session, account.household_id, standing.source_id)
    )
    return BillsSpaceFunding(
        balance_pence=balance, buffer_pence=buffer_pence, through=through,
        next_top_up=next_top_up,
        standing_rule_id=standing.source_id if standing and editable else None,
        standing_pence=(transfer_rules.fixed_amount_at(
            rules[standing.source_id], standing.expected_date,
        ) if standing else None),
        first_shortfall=first_shortfall,
        funding_parent_id=parent.id if parent else None,
        top_up_pence=max(0, buffer_pence - low), movements=rows,
    )


def _is_standing_top_up(rule_ids: set[int], movement: ExpectedMovement) -> bool:
    return (movement.source_type == ExpectedSource.transfer_rule
            and movement.confidence == Confidence.scheduled and movement.source_id in rule_ids)


def derived_space_rule(session: Session, household_id: int, rule_id: int) -> bool:
    payloads = session.scalars(select(AuditEvent.payload).where(
        AuditEvent.household_id == household_id, AuditEvent.entity_type == "transfer_rule",
        AuditEvent.entity_id == rule_id, AuditEvent.action == "create",
    ))
    return any(payload and json.loads(payload).get("derived") == "space_leg"
               for payload in payloads)
