"""add auth fields to users table

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on:    Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. password_hash ──────────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(255), nullable=True)
    )
    # Backfill existing rows with a placeholder (bcrypt hash of "changeme123")
    op.execute(
        "UPDATE users SET password_hash = "
        "'$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewYpfQN3YHCRbMHK' "
        "WHERE password_hash IS NULL"
    )
    op.alter_column("users", "password_hash", nullable=False)

    # ── 2. is_active ──────────────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=True)
    )
    op.execute("UPDATE users SET is_active = TRUE WHERE is_active IS NULL")
    op.alter_column("users", "is_active", nullable=False)

    # ── 3. last_login ─────────────────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True)
    )

    # ── 4. Enforce unique model_name on models table ──────────────────────────
    op.create_unique_constraint("uq_models_model_name", "models", ["model_name"])

    # ── 5. role default value ────────────────────────────────────────────────
    op.execute("UPDATE users SET role = 'employee' WHERE role IS NULL")


def downgrade() -> None:
    op.drop_constraint("uq_models_model_name", "models", type_="unique")
    op.drop_column("users", "last_login")
    op.drop_column("users", "is_active")
    op.drop_column("users", "password_hash")
