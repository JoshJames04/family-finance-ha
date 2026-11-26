"""Store the original date on the live bill movement when its due date changes."""

from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0077_bill_occurrence_origin"
down_revision = "0076_statement_bank_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("expected_movement", sa.Column("original_expected_date", sa.Date()))
    conn = op.get_bind()
    # Old events identify movements by a reusable SQLite integer. Recover a
    # chain only within this row's lifetime and after the bill's last create
    # or delete. An event in the row's birth second has ambiguous ordering.
    events = conn.execute(sa.text("""
        SELECT m.id, m.expected_date, json_extract(e.payload, '$.from') AS previous,
               json_extract(e.payload, '$.to') AS target,
               julianday(e.created_at) = julianday(m.created_at) AS ambiguous
        FROM expected_movement m JOIN audit_event e
          ON e.household_id = m.household_id AND e.entity_id = m.id
        WHERE m.source_type = 'commitment' AND e.entity_type = 'expected_movement'
          AND e.action = 'reschedule' AND json_valid(e.payload)
          AND julianday(e.created_at) >= julianday(m.created_at)
          AND e.id > COALESCE((
              SELECT MAX(b.id) FROM audit_event b
              WHERE b.household_id = m.household_id AND b.entity_type = 'recurring_commitment'
                AND b.entity_id = m.source_id AND b.action IN ('create', 'delete')
          ), 0)
        ORDER BY e.id DESC
    """))
    origins: dict[int, str] = {}
    ambiguous: set[int] = set()
    for mid, current, previous, target, same_second in events:
        if target != origins.get(mid, current) or not isinstance(previous, str):
            continue
        try:
            if date.fromisoformat(previous) >= date.fromisoformat(target):
                continue
        except ValueError:
            continue
        if same_second:
            ambiguous.add(mid)
        origins[mid] = previous
    updates = [{"id": mid, "origin": original} for mid, original in origins.items()
               if mid not in ambiguous]
    if updates:
        conn.execute(sa.text("UPDATE expected_movement SET original_expected_date = :origin "
                             "WHERE id = :id"), updates)


def downgrade() -> None:
    op.drop_column("expected_movement", "original_expected_date")
