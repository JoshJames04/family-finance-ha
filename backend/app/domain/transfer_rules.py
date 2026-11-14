"""Configurable transfer rules (issue #86).

A rule expresses one recurring household flow — e.g. "30% of salary to
the joint account" or "the food budget line to the food account" —
generically, so new flows need no code. Amount derivation is one of
three kinds encoded in ``TransferRule.amount_spec`` (JSON):

- ``fixed``: literal pence per occurrence → ``scheduled`` confidence.
- ``income_pct``: basis-points of income (positive inflow) received
  into the source account between the previous occurrence and this
  one → ``estimated`` (depends on actuals).
- ``budget_line``: the covering approved budget's *effective* line for
  a category at each occurrence date → ``estimated``; recomputed when
  a budget revision is approved.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.recurring import advance
from app.domain.reporting import external_category_amounts
from app.persistence.models import (
    Account,
    AmountKind,
    BankAccountMapping,
    Budget,
    BudgetLine,
    BudgetStatus,
    Transaction,
    TransactionStatus,
    TransferRule,
)


class TransferRuleError(Exception):
    """Domain validation failure → API maps to 409."""


def space_parent(session: Session, account_id: int) -> Account | None:
    """If the account is a provider *space*, return its parent main
    account — a space shares ``external_uid`` with the parent mapping
    on the same connection (issue #345). ``None`` for ordinary
    accounts or unmapped spaces."""
    space = session.scalar(select(BankAccountMapping).where(
        BankAccountMapping.account_id == account_id,
        BankAccountMapping.external_kind == "space"))
    if space is None:
        return None
    parent = session.scalar(select(BankAccountMapping).where(
        BankAccountMapping.connection_id == space.connection_id,
        BankAccountMapping.external_uid == space.external_uid,
        BankAccountMapping.external_kind == "account",
        BankAccountMapping.account_id.is_not(None)))
    return session.get(Account, parent.account_id) if parent else None


@dataclass(frozen=True)
class AmountSpec:
    """Parsed ``amount_spec``. Exactly the fields of ``kind`` are set."""
    kind: AmountKind
    pence: int | None = None
    bps: int | None = None
    category_id: int | None = None


def parse_spec(raw: str | dict[str, Any]) -> AmountSpec:
    data: dict[str, Any] = (
        json.loads(raw) if isinstance(raw, str) else dict(raw))
    try:
        kind = AmountKind(str(data["kind"]))
    except (KeyError, ValueError) as e:
        raise TransferRuleError(f"unknown amount kind: {e}") from e
    if kind is AmountKind.fixed:
        pence = int(data["pence"])
        if pence <= 0:
            raise TransferRuleError("fixed spec needs positive pence")
        return AmountSpec(kind, pence=pence)
    if kind is AmountKind.income_pct:
        bps = int(data["bps"])
        if not 0 < bps <= 10000:
            raise TransferRuleError("income_pct bps must be 1–10000")
        return AmountSpec(kind, bps=bps)
    return AmountSpec(kind, category_id=int(data["category_id"]))


def occurrences(rule: TransferRule, horizon: date,
                today: date | None = None) -> list[date]:
    """Occurrence dates in (max(today, start_date) .. horizon]."""
    today = today or date.today()
    d = rule.start_date
    while d < today:
        d = advance(d, rule.frequency, rule.day_of_month)
    out: list[date] = []
    while d <= horizon and (rule.end_date is None or d <= rule.end_date):
        out.append(d)
        d = advance(d, rule.frequency, rule.day_of_month)
    return out


def _period_before(rule: TransferRule, occ: date) -> date:
    """The occurrence's look-back start: the prior scheduled date."""
    # Walk from start_date — correct through the anchored advance.
    prev = rule.start_date
    d = rule.start_date
    while d < occ:
        prev, d = d, advance(d, rule.frequency, rule.day_of_month)
    return prev


def evaluate(session: Session, rule: TransferRule,
             on: date) -> int | None:
    """Amount in pence for the occurrence on ``on``. ``None`` when the
    amount cannot be derived yet (no covering budget / no income)."""
    spec = parse_spec(rule.amount_spec)
    if spec.kind is AmountKind.fixed:
        return spec.pence
    if spec.kind is AmountKind.income_pct:
        since = _period_before(rule, on)
        amounts = external_category_amounts(
            Transaction.account_id == rule.source_account_id,
            Transaction.status != TransactionStatus.void,
            Transaction.date >= since,
            Transaction.date < on)
        income = session.execute(select(func.coalesce(func.sum(
            amounts.c.amount_pence), 0)).where(
                amounts.c.amount_pence > 0, amounts.c.refund_of_id.is_(None))).scalar_one()
        assert spec.bps is not None
        return int(income) * spec.bps // 10000
    # budget_line: covering approved budget's effective line
    line = session.execute(
        select(BudgetLine).join(Budget).where(
            Budget.household_id == rule.household_id,
            Budget.status == BudgetStatus.approved,
            Budget.start_date <= on,
            Budget.end_date >= on,
            BudgetLine.category_id == spec.category_id)
        .order_by(Budget.start_date.desc()).limit(1)).scalars().first()
    return line.effective_pence if line is not None else None
