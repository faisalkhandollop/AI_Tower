"""j1k2l3m4n5o6_phase6_support_access_workflow

Revision ID: j1k2l3m4n5o6
Revises: i1j2k3l4m5n6
Create Date: 2026-06-15

Phase 6 — Support Access Workflow
Creates two new tables:
  support_access_requests
  support_access_logs

Also creates the ENUMs they depend on:
  support_access_status_enum
  support_access_log_action_enum

No existing tables are modified.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ──────────────────────────────────────────────────────
revision = "j1k2l3m4n5o6"
down_revision = "i1j2k3l4m5n6"
branch_labels = None
depends_on = None


# ── ENUM helpers ──────────────────────────────────────────────────────────────

def _create_enum(name: str, values: tuple, conn) -> None:
    """Create a PG ENUM only if it does not already exist."""
    existing = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = :name"),
        {"name": name},
    ).fetchone()
    if not existing:
        enum_values = ", ".join(f"'{v}'" for v in values)
        conn.execute(sa.text(f"CREATE TYPE {name} AS ENUM ({enum_values})"))


def _drop_enum(name: str, conn) -> None:
    conn.execute(sa.text(f"DROP TYPE IF EXISTS {name}"))


# ── upgrade ───────────────────────────────────────────────────────────────────

def upgrade() -> None:
    conn = op.get_bind()

    # ── ENUMs ─────────────────────────────────────────────────────────────────
    _create_enum(
        "support_access_status_enum",
        ("pending", "approved", "denied", "revoked", "expired"),
        conn,
    )
    _create_enum(
        "support_access_log_action_enum",
        (
            "REQUEST_CREATED",
            "REQUEST_APPROVED",
            "REQUEST_DENIED",
            "ACCESS_GRANTED",
            "ACCESS_REVOKED",
            "ACCESS_EXPIRED",
            "RESOURCE_ACCESSED",
        ),
        conn,
    )

    # ── support_access_requests ─────────────────────────────────────────────
    op.create_table(
        "support_access_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),

        sa.Column("ticket_reference", sa.String(200), nullable=True),
        sa.Column("reason", sa.Text, nullable=False),

        sa.Column("requested_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requester_email", sa.String(255), nullable=True),
        sa.Column("requester_role", sa.String(50), nullable=True),

        sa.Column("requested_scopes", sa.JSON, nullable=False),
        sa.Column("requested_minutes", sa.JSON, nullable=False),

        sa.Column(
            "status",
            sa.Enum(
                "pending", "approved", "denied", "revoked", "expired",
                name="support_access_status_enum", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),

        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reviewer_email", sa.String(255), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("granted_scopes", sa.JSON, nullable=True),
        sa.Column("granted_to", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("grantee_email", sa.String(255), nullable=True),
        sa.Column("access_token_jti", sa.String(64), nullable=True, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("revoked_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.Text, nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_support_access_requests_org_id", "support_access_requests", ["organization_id"])
    op.create_index("ix_support_access_requests_status", "support_access_requests", ["status"])
    op.create_index("ix_support_access_requests_requested_by", "support_access_requests", ["requested_by"])
    op.create_index("ix_support_access_requests_granted_to", "support_access_requests", ["granted_to"])
    op.create_index("ix_support_access_requests_jti", "support_access_requests", ["access_token_jti"])

    # ── support_access_logs ──────────────────────────────────────────────────
    op.create_table(
        "support_access_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("support_access_requests.id"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=False),

        sa.Column(
            "action",
            sa.Enum(
                "REQUEST_CREATED", "REQUEST_APPROVED", "REQUEST_DENIED",
                "ACCESS_GRANTED", "ACCESS_REVOKED", "ACCESS_EXPIRED",
                "RESOURCE_ACCESSED",
                name="support_access_log_action_enum", create_type=False,
            ),
            nullable=False,
        ),

        sa.Column("actor_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_email", sa.String(255), nullable=True),
        sa.Column("actor_role", sa.String(50), nullable=True),

        sa.Column("resource_type", sa.String(100), nullable=True),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("endpoint_path", sa.String(500), nullable=True),
        sa.Column("http_method", sa.String(10), nullable=True),

        sa.Column("detail", sa.Text, nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_support_access_logs_request_id", "support_access_logs", ["request_id"])
    op.create_index("ix_support_access_logs_org_id", "support_access_logs", ["organization_id"])
    op.create_index("ix_support_access_logs_action", "support_access_logs", ["action"])
    op.create_index("ix_support_access_logs_timestamp", "support_access_logs", ["timestamp"])


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    op.drop_table("support_access_logs")
    op.drop_table("support_access_requests")

    _drop_enum("support_access_log_action_enum", conn)
    _drop_enum("support_access_status_enum", conn)
