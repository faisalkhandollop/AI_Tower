"""add sessions table and session_id to usage_logs

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── NOTE: sessions table was already created by Base.metadata.create_all()
    # before this migration ran, so we skip create_table and only ensure
    # the indexes exist + add session_id to usage_logs.

    # ── 1. Indexes on sessions (CREATE IF NOT EXISTS is safe to re-run) ──────
    op.execute("CREATE INDEX IF NOT EXISTS ix_sessions_user_id     ON sessions(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sessions_last_active ON sessions(last_active)")

    # ── 2. Add session_id column to usage_logs ───────────────────────────────
    op.execute("""
        ALTER TABLE usage_logs
        ADD COLUMN IF NOT EXISTS session_id VARCHAR(100)
        REFERENCES sessions(session_id)
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_usage_logs_session_id ON usage_logs(session_id)")


def downgrade() -> None:
    op.drop_index("ix_usage_logs_session_id", table_name="usage_logs")
    op.drop_column("usage_logs", "session_id")

    op.drop_index("ix_sessions_last_active", table_name="sessions")
    op.drop_index("ix_sessions_user_id",     table_name="sessions")
    op.drop_table("sessions")