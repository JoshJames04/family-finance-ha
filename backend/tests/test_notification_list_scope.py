"""Notification list visibility agrees with administrator object access."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.settings import Settings
from app.main import create_app
from app.persistence.models import (
    Household,
    NotificationDelivery,
    NotificationEvent,
    NotificationRule,
    UserProfile,
)


def test_notification_lists_preserve_admin_member_viewer_and_household_scope(tmp_path):
    app = create_app(Settings.load({
        "FF_DATA_DIR": str(tmp_path), "FF_SCHEDULER_TICK_SECONDS": "0",
        "FF_BACKUP_INTERVAL_HOURS": "0",
    }))
    with TestClient(app) as client:
        with Session(app.state.engine) as session:
            home, foreign = Household(name="Home"), Household(name="Other")
            session.add_all([home, foreign])
            session.flush()
            profiles = [UserProfile(household_id=home.id, display_name=role, role=role)
                        for role in ("admin", "member", "viewer")]
            session.add_all(profiles)
            session.flush()
            ids = [p.id for p in profiles]
            hid = home.id
            rules = [NotificationRule(
                household_id=household_id, name=f"Rule {i}", kind="balance_below",
                target_profile_id=target,
            ) for i, (household_id, target) in enumerate([
                (hid, None), (hid, ids[0]), (hid, ids[1]), (hid, ids[2]),
                (foreign.id, None),
            ])]
            session.add_all(rules)
            session.flush()
            rule_ids = [r.id for r in rules]
            for rule in rules:
                delivery = NotificationDelivery(
                    household_id=rule.household_id, rule_id=rule.id,
                    channel="ha_notify", event_count=1,
                )
                session.add(delivery)
                session.flush()
                session.add(NotificationEvent(
                    rule_id=rule.id, delivery_id=delivery.id, dedup_key="test",
                ))
            session.commit()
        for profile_id, visible in [(ids[0], rule_ids[:4]),
                                    (ids[1], [rule_ids[0], rule_ids[2]]),
                                    (ids[2], [rule_ids[0]])]:
            headers = {"X-Household-Id": str(hid), "X-Profile-Id": str(profile_id)}
            for resource in ("rules", "events", "deliveries"):
                response = client.get(f"/api/v1/notifications/{resource}", headers=headers)
                assert response.status_code == 200
                key = "id" if resource == "rules" else "rule_id"
                assert {row[key] for row in response.json()} == set(visible), resource
