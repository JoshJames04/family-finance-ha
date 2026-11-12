import json
import logging
import sys
from datetime import date

from app.core.logging import JsonFormatter
from app.domain import categorise, outbox, settle
from app.persistence.models import Account, Category, Transaction, TransactionStatus


def account(session, household, name):
    row = Account(
        household_id=household.id,
        name=name,
        type="personal",
        opening_balance_pence=0,
        opening_date=date(2025, 1, 1),
    )
    session.add(row)
    session.flush()
    return row


def tx(session, a, name, amount):
    row = Transaction(
        account_id=a.id,
        date=date(2025, 4, 10),
        amount_pence=amount,
        payee=name,
        status=TransactionStatus.cleared,
        source="starling",
    )
    session.add(row)
    session.flush()
    return row


def test_equal_amount_is_not_transfer_evidence(session, household):
    a = account(session, household, "Main")
    b = account(session, household, "Bills")
    debit = tx(session, a, "Sky", -19500)
    credit = tx(session, b, "YouFibre", 19500)
    assert categorise.auto_pair_transfers(session, household.id) == 0
    assert debit.transfer_id is credit.transfer_id is None


def test_ambiguous_transfer_legs_are_not_paired(session, household):
    a = account(session, household, "Main")
    b = account(session, household, "Bills")
    tx(session, a, "Transfer", -19500)
    tx(session, b, "Transfer", 19500)
    tx(session, b, "Transfer", 19500)
    assert categorise.auto_pair_transfers(session, household.id) == 0


def test_owned_account_names_classify_durably_without_renaming(session, household):
    a = account(session, household, "Josh Main")
    b = account(session, household, "Personal Bills")
    category = Category(household_id=household.id, name="Internal Transfers")
    session.add(category)
    session.flush()
    for day in (10, 11):
        debit = tx(session, a, b.name, -19500)
        credit = tx(session, b, a.name, 19500)
        debit.date = credit.date = date(2025, 4, day)
        session.flush()
        settle.post_sync(session, household.id, today=date(2025, 4, day))
        assert debit.category_id == credit.category_id == category.id
        assert debit.transfer_id == credit.transfer_id
        assert debit.payee == b.name and credit.payee == a.name


def test_safe_settlement_payload_hides_remainder(session, household):
    row = outbox.emit(
        session,
        household.id,
        "movement.settled",
        payload={"matched_pence_outstanding": 500, "manual": False},
    )
    assert outbox.visible_payload(row, False) == {"manual": False}


def test_exception_and_nested_secrets_redacted():
    record = logging.LogRecord("test", logging.ERROR, "", 1, "token=synthetic-message", (), None)
    try:
        raise ValueError("password=synthetic-traceback")
    except ValueError:
        record.exc_info = sys.exc_info()
    record.extra = {"nested": {"secret": "synthetic-extra"}}
    payload = json.loads(JsonFormatter().format(record))
    assert "synthetic-message" not in payload["msg"]
    assert "synthetic-traceback" not in payload["exc"]
    assert payload["nested"]["secret"] == "***"


def test_overspend_uses_owner_scoped_actuals(session, household):
    from app.domain import notifications
    from app.persistence.models import (
        Budget,
        BudgetLine,
        BudgetLineSide,
        BudgetPeriodType,
        BudgetStatus,
        NotificationKind,
        Role,
        UserProfile,
    )

    owner = UserProfile(household_id=household.id, display_name="Owner", role=Role.admin)
    other = UserProfile(household_id=household.id, display_name="Other", role=Role.member)
    session.add_all([owner, other])
    session.flush()
    a = account(session, household, "Private")
    a.owner_profile_id = other.id
    category = Category(household_id=household.id, name="Test")
    session.add(category)
    session.flush()
    budget = Budget(
        household_id=household.id,
        name="Owner budget",
        owner_profile_id=owner.id,
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
        status=BudgetStatus.approved,
    )
    session.add(budget)
    session.flush()
    session.add(
        BudgetLine(
            budget_id=budget.id,
            category_id=category.id,
            side=BudgetLineSide.expense,
            original_pence=100,
        )
    )
    transaction = tx(session, a, "Other purchase", -500)
    transaction.category_id = category.id
    session.flush()
    rule = notifications.create_rule(
        session,
        household.id,
        name="Owner alert",
        kind=NotificationKind.budget_overspend,
        params={},
        target_profile_id=owner.id,
    )
    assert notifications.evaluate(session, rule, today=date(2025, 4, 10)) == 0


def test_explicit_scoped_rule_does_not_generalise_apple(session, household):
    from app.persistence.models import CategorisationRule, MatchType, RuleSource

    a = account(session, household, "Main")
    b = account(session, household, "Bills")
    category = Category(household_id=household.id, name="Everyday Spending")
    session.add(category)
    session.flush()
    rule = CategorisationRule(
        household_id=household.id,
        pattern="Apple",
        match_type=MatchType.exact,
        category_id=category.id,
        source=RuleSource.manual,
        auto_apply=True,
        account_id=a.id,
        amount_pence=-99,
    )
    session.add(rule)
    small = tx(session, a, "Apple", -99)
    music = tx(session, a, "Apple", -1099)
    cloud = tx(session, b, "Apple", -99)
    categorise.apply_rules(session, household.id)
    assert small.category_id == category.id
    assert music.category_id is cloud.category_id is None


def test_real_routes_reject_viewer_privileged_writes(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence.models import Household, Role, UserProfile

    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            h = Household(name="Synthetic")
            session.add(h)
            session.flush()
            admin = UserProfile(household_id=h.id, display_name="Admin", role=Role.admin)
            viewer = UserProfile(household_id=h.id, display_name="Viewer", role=Role.viewer)
            session.add_all([admin, viewer])
            session.flush()
            headers = {"X-Household-Id": str(h.id), "X-Profile-Id": str(viewer.id)}
            session.commit()
        assert (
            client.post(
                "/api/v1/profiles",
                headers=headers,
                json={"display_name": "Escalation", "role": "admin"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/credentials",
                headers=headers,
                json={"ref": "synthetic", "secret": "synthetic"},
            ).status_code
            == 403
        )
        assert client.get("/api/v1/credentials", headers=headers).status_code == 403
        assert (
            client.post(
                "/api/v1/notifications/rules",
                headers=headers,
                json={"name": "Synthetic", "kind": "spend_above", "params": {}},
            ).status_code
            == 403
        )


def test_private_outbox_events_hidden_in_both_modes(session, household):
    from app.persistence.models import Role, UserProfile

    owner = UserProfile(household_id=household.id, display_name="Owner", role=Role.member)
    session.add(owner)
    session.flush()
    a = account(session, household, "Private")
    a.owner_profile_id = owner.id
    transaction = tx(session, a, "Private purchase", -500)
    event = outbox.emit(
        session,
        household.id,
        "transaction.imported",
        entity_kind="transaction",
        entity_id=transaction.id,
        payload={"payee": "Private purchase", "date": "2025-04-10", "amount_pence": -500},
    )
    assert not outbox.event_visible(session, event, None)
    assert not outbox.event_visible(session, event, owner.id + 1)
    assert outbox.event_visible(session, event, owner.id)


def test_owned_alias_rules_pair_without_guessing_person_names(session, household):
    from app.persistence.models import CategorisationRule, MatchType, RuleSource

    a = account(session, household, "Current (GBP)")
    b = account(session, household, "Personal Bills")
    category = Category(household_id=household.id, name="Internal Transfers")
    session.add(category)
    session.flush()
    for source, target, name in ((a, b, "Personal Bills"), (b, a, "Synthetic Main")):
        session.add(
            CategorisationRule(
                household_id=household.id,
                pattern=name,
                match_type=MatchType.exact,
                category_id=category.id,
                source=RuleSource.manual,
                auto_apply=True,
                account_id=source.id,
                transfer_account_id=target.id,
            )
        )
    debit = tx(session, a, "Personal Bills", -19500)
    credit = tx(session, b, "Synthetic Main", 19500)
    settle.post_sync(session, household.id, today=date(2025, 4, 10))
    assert debit.transfer_id is not None and debit.transfer_id == credit.transfer_id
    assert debit.category_id == credit.category_id == category.id
    assert credit.payee == "Synthetic Main"


def test_exception_message_cannot_export_private_financial_details():
    record = logging.LogRecord("test", logging.ERROR, "", 1, "Operation failed", (), None)
    try:
        raise ValueError("Synthetic private counterparty balance=12345")
    except ValueError:
        record.exc_info = sys.exc_info()
    payload = json.loads(JsonFormatter().format(record))
    assert payload["exc"] == "ValueError"
    assert "12345" not in json.dumps(payload)
    assert "Synthetic private counterparty" not in json.dumps(payload)


def test_overspend_split_and_explicit_zero_revision(session, household):
    from app.domain import notifications
    from app.persistence.models import (
        Budget,
        BudgetLine,
        BudgetLineSide,
        BudgetPeriodType,
        BudgetStatus,
        NotificationKind,
        TransactionSplit,
    )

    a = account(session, household, "Shared")
    category = Category(household_id=household.id, name="Budget category")
    other = Category(household_id=household.id, name="Other category")
    session.add_all([category, other])
    session.flush()
    budget = Budget(
        household_id=household.id,
        name="Shared budget",
        period_type=BudgetPeriodType.monthly,
        start_date=date(2025, 4, 1),
        end_date=date(2025, 4, 30),
        status=BudgetStatus.approved,
    )
    session.add(budget)
    session.flush()
    line = BudgetLine(
        budget_id=budget.id,
        category_id=category.id,
        side=BudgetLineSide.expense,
        original_pence=1000,
        revised_pence=0,
    )
    session.add(line)
    transaction = tx(session, a, "Split purchase", -100)
    transaction.category_id = other.id
    session.add_all(
        [
            TransactionSplit(
                transaction_id=transaction.id, category_id=category.id, amount_pence=-50
            ),
            TransactionSplit(transaction_id=transaction.id, category_id=other.id, amount_pence=-50),
        ]
    )
    session.flush()
    rule = notifications.create_rule(
        session,
        household.id,
        name="Split overspend",
        kind=NotificationKind.budget_overspend,
        params={},
    )
    assert notifications.evaluate(session, rule, today=date(2025, 4, 10)) == 1


def test_specific_scoped_policy_precedes_unscoped_learned_suggestion(session, household):
    from app.persistence.models import CategorisationRule, MatchType, RuleSource

    a = account(session, household, "Main")
    category = Category(household_id=household.id, name="Everyday Spending")
    suggestion = Category(household_id=household.id, name="Subscriptions")
    session.add_all([category, suggestion])
    session.flush()
    session.add_all(
        [
            CategorisationRule(
                household_id=household.id,
                pattern="Apple",
                match_type=MatchType.exact,
                category_id=suggestion.id,
                source=RuleSource.learned,
                times_confirmed=100,
            ),
            CategorisationRule(
                household_id=household.id,
                pattern="Apple",
                match_type=MatchType.exact,
                category_id=category.id,
                source=RuleSource.manual,
                auto_apply=True,
                account_id=a.id,
                amount_pence=-99,
            ),
        ]
    )
    transaction = tx(session, a, "Apple", -99)
    categorise.apply_rules(session, household.id)
    assert transaction.category_id == category.id


def test_anonymous_ingress_is_denied_on_resolved_profile_routes(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence.models import Household, Role, UserProfile

    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_INGRESS": "true",
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            h = Household(name="Synthetic ingress")
            session.add(h)
            session.flush()
            session.add(
                UserProfile(
                    household_id=h.id,
                    display_name="Admin",
                    role=Role.admin,
                    ha_user_id="synthetic-admin",
                )
            )
            session.commit()
        for method, path, body in [
            ("GET", "/api/v1/accounts", None),
            ("POST", "/api/v1/profiles", {"display_name": "Escalation", "role": "admin"}),
            (
                "POST",
                "/api/v1/notifications/rules",
                {"name": "Synthetic", "kind": "spend_above", "params": {}},
            ),
        ]:
            assert client.request(method, path, json=body).status_code in {401, 403}


def test_ingress_service_scope_and_viewer_deny_matrix(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session

    import app.api.auth as auth
    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence.models import AuditEvent, Household, Role, UserProfile

    monkeypatch.setattr(auth, "SUPERVISOR_IP", "testclient")
    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_INGRESS": "true",
                "FF_ADMIN_TOKEN": "synthetic-service-token",
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            h = Household(name="Synthetic ingress")
            session.add(h)
            session.flush()
            admin = UserProfile(
                household_id=h.id,
                display_name="Admin",
                role=Role.admin,
                ha_user_id="synthetic-admin",
            )
            viewer = UserProfile(
                household_id=h.id,
                display_name="Viewer",
                role=Role.viewer,
                ha_user_id="synthetic-viewer",
            )
            session.add_all([admin, viewer])
            session.flush()
            shared = account(session, h, "Shared")
            private = account(session, h, "Private")
            private.owner_profile_id = admin.id
            sid, pid = shared.id, private.id
            session.commit()
        viewer_headers = {"X-Remote-User-Id": "synthetic-viewer"}
        for path, body in [
            ("/api/v1/profiles", {"display_name": "Injected", "role": "admin"}),
            ("/api/v1/categories", {"name": "Injected category"}),
            ("/api/v1/credentials", {"ref": "Synthetic", "secret": "Synthetic"}),
            (
                "/api/v1/notifications/rules",
                {"name": "Synthetic", "kind": "spend_above", "params": {}},
            ),
        ]:
            assert client.post(path, json=body).status_code == 401
            assert client.post(path, headers=viewer_headers, json=body).status_code == 403
        service = {"X-Admin-Token": "synthetic-service-token"}
        assert client.get("/api/v1/accounts", headers=service).json()["total"] == 1
        assert client.get(f"/api/v1/accounts/{pid}/balance", headers=service).status_code == 404
        assert client.get("/api/v1/categories", headers=service).status_code == 403
        assert client.get("/api/v1/accounts", headers={"X-Admin-Token": "wrong"}).status_code == 401
        assert (
            client.post(
                "/api/v1/profiles",
                headers=service,
                json={"display_name": "Injected", "role": "admin"},
            ).status_code
            == 403
        )
        payload = {
            "account_id": sid,
            "date": "2025-04-10",
            "amount": -99,
            "payee": "Synthetic merchant",
        }
        assert client.post("/api/v1/transactions", headers=service, json=payload).status_code == 201
        payload["account_id"] = pid
        assert client.post("/api/v1/transactions", headers=service, json=payload).status_code == 404
        with Session(app.state.engine) as session:
            assert session.scalar(select(func.count(UserProfile.id))) == 2
            assert (
                session.scalar(
                    select(AuditEvent.actor).where(
                        AuditEvent.action == "create", AuditEvent.entity_type == "transaction"
                    )
                )
                == "ha-service"
            )


def test_every_openapi_protected_route_rejects_anonymous_ingress(tmp_path):
    import re

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from app.core.settings import Settings
    from app.main import create_app
    from app.persistence.models import Household, Role, UserProfile

    app = create_app(
        Settings.load(
            {
                "FF_DATA_DIR": str(tmp_path),
                "FF_INGRESS": "true",
                "FF_SCHEDULER_TICK_SECONDS": "0",
                "FF_BACKUP_INTERVAL_HOURS": "0",
            }
        )
    )
    public = {
        ("get", "/api/v1/version"),
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/logout"),
        ("post", "/api/v1/integrations/starling/webhook"),
    }
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            h = Household(name="Synthetic ingress")
            session.add(h)
            session.flush()
            session.add(
                UserProfile(
                    household_id=h.id,
                    display_name="Admin",
                    role=Role.admin,
                    ha_user_id="synthetic-admin",
                )
            )
            session.commit()
        checked = 0
        for path, operations in app.openapi()["paths"].items():
            if not path.startswith("/api/v1/"):
                continue
            for method in operations:
                if (
                    method not in {"get", "post", "put", "patch", "delete"}
                    or (method, path) in public
                ):
                    continue
                concrete = re.sub(r"\{[^}]+\}", "1", path)
                response = client.request(method, concrete)
                assert response.status_code == 401, (method, path, response.status_code)
                checked += 1
        assert checked > 100


def test_shared_notification_rule_cannot_scan_private_account(session, household):
    from app.domain import notifications
    from app.persistence.models import NotificationKind, Role, UserProfile

    owner = UserProfile(household_id=household.id, display_name="Private owner", role=Role.member)
    session.add(owner)
    session.flush()
    a = account(session, household, "Private")
    a.owner_profile_id = owner.id
    tx(session, a, "Private merchant", -500)
    rule = notifications.create_rule(
        session,
        household.id,
        name="Shared alert",
        kind=NotificationKind.spend_above,
        params={"threshold_pence": 100},
    )
    assert notifications.evaluate(session, rule) == 0


def test_salary_reminder_keeps_employment_owner_on_shared_account(session, household):
    from app.domain import notifications
    from app.persistence.models import (
        Confidence,
        Employment,
        ExpectedMovement,
        ExpectedSource,
        NotificationKind,
        UserProfile,
    )

    owner = UserProfile(household_id=household.id, display_name="Salary owner")
    session.add(owner)
    session.flush()
    a = account(session, household, "Shared salary destination")
    employment = Employment(
        household_id=household.id,
        profile_id=owner.id,
        employer="Synthetic employer",
        jurisdiction="england_wales",
        contracted_hours=37,
    )
    session.add(employment)
    session.flush()
    session.add(
        ExpectedMovement(
            household_id=household.id,
            account_id=a.id,
            expected_date=date(2025, 4, 11),
            amount_pence=10000,
            label="Salary",
            source_type=ExpectedSource.employment,
            source_id=employment.id,
            confidence=Confidence.estimated,
        )
    )
    session.flush()
    rule = notifications.create_rule(
        session,
        household.id,
        name="Shared payday",
        kind=NotificationKind.payday_reminder,
        params={"days_before": 1},
    )
    assert notifications.evaluate(session, rule, today=date(2025, 4, 10)) == 0
    rule.target_profile_id = owner.id
    assert notifications.evaluate(session, rule, today=date(2025, 4, 10)) == 1
