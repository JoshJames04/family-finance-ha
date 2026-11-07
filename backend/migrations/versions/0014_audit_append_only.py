"""audit_event append-only triggers (issue #31).

UPDATE/DELETE on audit_event abort at the database layer so the log is
tamper-evident even to code holding a raw connection.

Revision ID: 0014_audit_append_only
Revises: 0013_rollover
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_audit_append_only"
down_revision: str | None = "0013_rollover"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TRIGGER audit_event_no_update
        BEFORE UPDATE ON audit_event
        BEGIN
            SELECT RAISE(ABORT, 'audit_event is append-only');
        END
    """))
    op.execute(sa.text("""
        CREATE TRIGGER audit_event_no_delete
        BEFORE DELETE ON audit_event
        BEGIN
            SELECT RAISE(ABORT, 'audit_event is append-only');
        END
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_event_no_delete"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS audit_event_no_update"))
