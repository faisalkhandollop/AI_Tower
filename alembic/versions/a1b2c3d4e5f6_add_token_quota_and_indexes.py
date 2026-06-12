"""add token_quota to users and indexes on usage_logs

Revision ID: a1b2c3d4e5f6
Revises: 6de7c13066e0
Create Date: 2026-06-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '6de7c13066e0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Add token_quota to users ──────────────────────────────────────────
    # Add as nullable first, backfill, then set NOT NULL — safe on live tables.
    op.add_column(
        "users",
        sa.Column("token_quota", sa.Integer(), nullable=True)
    )
    op.execute("UPDATE users SET token_quota = 100000 WHERE token_quota IS NULL")
    op.alter_column("users", "token_quota", nullable=False)

    # ── 2. Indexes on usage_logs hot columns ─────────────────────────────────
    op.create_index("ix_usage_logs_user_id",    "usage_logs", ["user_id"])
    op.create_index("ix_usage_logs_provider",   "usage_logs", ["provider"])
    op.create_index("ix_usage_logs_model",      "usage_logs", ["model"])
    op.create_index("ix_usage_logs_created_at", "usage_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_usage_logs_created_at", table_name="usage_logs")
    op.drop_index("ix_usage_logs_model",      table_name="usage_logs")
    op.drop_index("ix_usage_logs_provider",   table_name="usage_logs")
    op.drop_index("ix_usage_logs_user_id",    table_name="usage_logs")
    op.drop_column("users", "token_quota")
