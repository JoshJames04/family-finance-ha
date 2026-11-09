"""Transaction splits — validation, rollup and correction (issue #289)."""

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.domain import splits
from app.domain.splits import SplitError
from app.domain.transactions import correct as txn_correct
from app.persistence.models import (
    Account,
    AccountType,
    Category,
    Household,
    Transaction,
    TransactionStatus,
)


@pytest.fixture
def household(session: Session) -> Household:
    h = Household(name="Test Household")
    session.add(h)
    session.flush()
    return h


@pytest.fixture
def account(session: Session, household: Household) -> Account:
    a = Account(
        household=household, name="Joint", type=AccountType.joint,
        currency="GBP", opening_balance_pence=0,
        opening_date=date(2026, 1, 1))
    session.add(a)
    session.flush()
    return a


@pytest.fixture
def categories(session: Session, household: Household) -> dict[str, int]:
    cats = {}
    for name in ("Groceries", "Household", "Gifts"):
        c = Category(household_id=household.id, name=name)
        session.add(c)
        session.flush()
        cats[name] = c.id
    return cats


def _txn(account: Account, **kw) -> Transaction:
    t = Transaction(
        account_id=account.id,
        date=date(2026, 3, 5),
        amount_pence=kw.pop("amount_pence", -1299),
        payee=kw.pop("payee", "Tesco"),
        **kw)
    return t


class TestReplace:
    def test_valid_split_set(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        rows = splits.replace(session, t, household.id, [
            {"category_id": categories["Groceries"],
             "amount_pence": -800},
            {"category_id": categories["Gifts"],
             "amount_pence": -499}])
        assert len(rows) == 2
        assert [r.position for r in rows] == [0, 1]
        assert len(t.splits) == 2

    def test_sum_must_equal_parent(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        with pytest.raises(SplitError, match="does not equal"):
            splits.replace(session, t, household.id, [
                {"category_id": categories["Groceries"],
                 "amount_pence": -800},
                {"category_id": categories["Gifts"],
                 "amount_pence": -400}])

    def test_single_leg_rejected(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        with pytest.raises(SplitError, match="single split"):
            splits.replace(session, t, household.id, [
                {"category_id": categories["Groceries"],
                 "amount_pence": -1299}])

    def test_wrong_sign_rejected(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        with pytest.raises(SplitError, match="same.*sign|sign"):
            splits.replace(session, t, household.id, [
                {"category_id": categories["Groceries"],
                 "amount_pence": -1800},
                {"category_id": categories["Gifts"],
                 "amount_pence": 501}])

    def test_unknown_category_rejected(
            self, session: Session, account: Account,
            household: Household) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        with pytest.raises(SplitError, match="unknown or foreign"):
            splits.replace(session, t, household.id, [
                {"category_id": 9999, "amount_pence": -699},
                {"category_id": 9998, "amount_pence": -600}])

    def test_empty_clears(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        t = _txn(account)
        session.add(t)
        session.flush()
        splits.replace(session, t, household.id, [
            {"category_id": categories["Groceries"],
             "amount_pence": -800},
            {"category_id": categories["Gifts"],
             "amount_pence": -499}])
        assert splits.replace(session, t, household.id, []) == []
        assert t.splits == []

    def test_reconciled_refuses(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        t = _txn(account, status=TransactionStatus.reconciled)
        session.add(t)
        session.flush()
        from app.domain.periods import ReconciledImmutableError
        with pytest.raises(ReconciledImmutableError):
            splits.replace(session, t, household.id, [
                {"category_id": categories["Groceries"],
                 "amount_pence": -800},
                {"category_id": categories["Gifts"],
                 "amount_pence": -499}])


class TestCorrection:
    def test_splits_carry_to_corrected_row(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        from app.persistence.models import TransactionSplit
        t = _txn(account, status=TransactionStatus.reconciled)
        session.add(t)
        session.flush()
        t.splits.append(TransactionSplit(
            category_id=categories["Groceries"], amount_pence=-800,
            position=0))
        t.splits.append(TransactionSplit(
            category_id=categories["Gifts"], amount_pence=-499,
            position=1))
        session.flush()
        corrected = txn_correct(session, t, "mis-split")
        assert len(corrected.splits) == 2
        assert sorted(
            s.amount_pence for s in corrected.splits) == [-800, -499]
        assert sum(s.amount_pence
                   for s in corrected.splits) == -1299


class TestEffectiveAmounts:
    def test_split_parent_counts_legs_not_whole(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        """A -£12.99 shop split -£8 groceries/-£4.99 gifts reports as
        two legs; an unsplit row counts as its own category."""
        t = _txn(account)
        session.add(t)
        session.flush()
        from app.persistence.models import TransactionSplit
        t.splits.append(TransactionSplit(
            category_id=categories["Groceries"], amount_pence=-800,
            position=0))
        t.splits.append(TransactionSplit(
            category_id=categories["Gifts"], amount_pence=-499,
            position=1))
        u = _txn(account, amount_pence=-500, payee="Other",
                 category_id=categories["Groceries"])
        session.add(u)
        session.flush()
        from sqlalchemy import func, select

        from app.persistence.models import Account as Acct
        from app.persistence.models import TransactionStatus
        ea = splits.effective_category_amounts(
            Acct.household_id == household.id,
            Transaction.status != TransactionStatus.void)
        rows = session.execute(
            select(ea.c.category_id, func.sum(ea.c.amount_pence))
            .group_by(ea.c.category_id)).all()
        got = {cid: total for cid, total in rows}
        assert got[categories["Groceries"]] == -1300  # -800 leg + -500 unsplit
        assert got[categories["Gifts"]] == -499
        assert len(got) == 2  # parent amount never double-counted


class TestBudgetRollup:
    def test_split_legs_hit_their_own_lines(
            self, session: Session, account: Account,
            household: Household, categories: dict[str, int]) -> None:
        """A split shop reports each leg against its own budget line —
        the AC: split transactions report correctly in variance."""
        from app.domain import budgets
        from app.persistence.models import (
            Budget,
            BudgetLine,
            BudgetLineSide,
            BudgetPeriodType,
            BudgetStatus,
            TransactionSplit,
        )
        b = Budget(household_id=household.id, name="March",
                   period_type=BudgetPeriodType.monthly,
                   start_date=date(2026, 3, 1),
                   end_date=date(2026, 3, 31),
                   status=BudgetStatus.approved)
        session.add(b)
        session.flush()
        gl = BudgetLine(budget_id=b.id,
                        category_id=categories["Groceries"],
                        side=BudgetLineSide.expense, original_pence=-1000)
        fl = BudgetLine(budget_id=b.id,
                        category_id=categories["Gifts"],
                        side=BudgetLineSide.expense, original_pence=-200)
        session.add_all([gl, fl])
        t = _txn(account)
        session.add(t)
        session.flush()
        t.splits.append(TransactionSplit(
            category_id=categories["Groceries"], amount_pence=-800,
            position=0))
        t.splits.append(TransactionSplit(
            category_id=categories["Gifts"], amount_pence=-499,
            position=1))
        u = _txn(account, amount_pence=-500, payee="Other",
                 category_id=categories["Groceries"])
        session.add(u)
        session.flush()
        actuals = budgets.actuals_for_budget(session, b)
        assert actuals[gl.id] == 1300   # -800 leg + -500 unsplit
        assert actuals[fl.id] == 499    # split leg only
