"""i1j2k3l4m5n6_phase5_privacy_enforcement

Revision ID: i1j2k3l4m5n6
Revises: h1i2j3k4l5m6
Create Date: 2026-06-12

Phase 5 — Privacy Enforcement
Creates four new tables:
  privacy_settings
  data_access_policies
  export_control_logs
  super_admin_access_blocks

Also creates the three ENUMs they depend on:
  chat_visibility_enum
  export_scope_enum
  export_format_enum
  export_decision_enum

No existing tables are modified.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ──────────────────────────────────────────────────────
revision = "i1j2k3l4m5n6"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


# ── ENUM helpers ──────────────────────────────────────────────────────────────

def _create_enum(name: str, values: tuple, conn) -> None:
    """Create a PG ENUM only if it does not already exist."""
    existing = conn.execute(
        sa.text(
            "SELECT 1 FROM pg_type WHERE typname = :name"
        ),
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
        "chat_visibility_enum",
        ("org_admin_only", "manager_and_above", "team_lead_and_above", "none"),
        conn,
    )
    _create_enum(
        "export_scope_enum",
        (
            "conversations",
            "messages",
            "usage_logs",
            "audit_logs",
            "user_list",
            "api_keys",
            "attachments",
            "knowledge_base",
        ),
        conn,
    )
    _create_enum("export_format_enum", ("json", "csv", "pdf"), conn)
    _create_enum("export_decision_enum", ("allowed", "blocked"), conn)

    # ── privacy_settings ──────────────────────────────────────────────────────
    op.create_table(
        "privacy_settings",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            unique=True,
        ),
        # Chat visibility
        sa.Column(
            "chat_visibility",
            sa.Enum(
                "org_admin_only", "manager_and_above",
                "team_lead_and_above", "none",
                name="chat_visibility_enum",
                create_type=False,
            ),
            nullable=False,
            server_default="org_admin_only",
        ),
        # Super admin block flags (always True; stored for assertion / audit)
        sa.Column("block_super_admin_conversations", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("block_super_admin_messages",      sa.Boolean, nullable=False, server_default="true"),
        sa.Column("block_super_admin_attachments",   sa.Boolean, nullable=False, server_default="true"),
        sa.Column("block_super_admin_kb",            sa.Boolean, nullable=False, server_default="true"),
        sa.Column("block_super_admin_api_keys",      sa.Boolean, nullable=False, server_default="true"),
        # Export controls
        sa.Column("exports_enabled",          sa.Boolean, nullable=False, server_default="true"),
        sa.Column("allowed_export_scopes",    sa.JSON,    nullable=False, server_default='["usage_logs","audit_logs","user_list"]'),
        sa.Column("export_requires_reason",   sa.Boolean, nullable=False, server_default="true"),
        sa.Column("export_notify_org_admin",  sa.Boolean, nullable=False, server_default="true"),
        # Data retention (days, 0 = forever)
        sa.Column("message_retention_days",    sa.Integer, nullable=False, server_default="0"),
        sa.Column("attachment_retention_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("usage_log_retention_days",  sa.Integer, nullable=False, server_default="365"),
        # Metadata
        sa.Column("last_updated_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
    )
    op.create_index("ix_privacy_settings_org_id", "privacy_settings", ["organization_id"])

    # ── data_access_policies ──────────────────────────────────────────────────
    op.create_table(
        "data_access_policies",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("name",                      sa.String(200),  nullable=False),
        sa.Column("description",               sa.Text,         nullable=True),
        sa.Column("permitted_data_categories", sa.JSON,         nullable=False, server_default="[]"),
        sa.Column("allowed_roles",             sa.JSON,         nullable=False, server_default="[]"),
        sa.Column("is_active",                 sa.Boolean,      nullable=False, server_default="true"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
        sa.UniqueConstraint("organization_id", "name", name="uq_data_access_policy_name"),
    )
    op.create_index("ix_data_access_policies_org_id", "data_access_policies", ["organization_id"])

    # ── export_control_logs ───────────────────────────────────────────────────
    op.create_table(
        "export_control_logs",
        sa.Column("id",              postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("requester_role",  sa.String(50),  nullable=False),
        sa.Column("requester_email", sa.String(255), nullable=True),
        sa.Column(
            "scope",
            sa.Enum(
                "conversations", "messages", "usage_logs", "audit_logs",
                "user_list", "api_keys", "attachments", "knowledge_base",
                name="export_scope_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "export_format",
            sa.Enum("json", "csv", "pdf", name="export_format_enum", create_type=False),
            nullable=False,
            server_default="json",
        ),
        sa.Column("filters_applied", sa.JSON,    nullable=True),
        sa.Column("reason",          sa.Text,    nullable=True),
        sa.Column(
            "decision",
            sa.Enum("allowed", "blocked", name="export_decision_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("block_reason",  sa.Text,    nullable=True),
        sa.Column("record_count",  sa.Integer, nullable=True),
        sa.Column("ip_address",    sa.String(45), nullable=True),
        sa.Column("timestamp",     sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_export_control_logs_org_id",    "export_control_logs", ["organization_id"])
    op.create_index("ix_export_control_logs_requester", "export_control_logs", ["requested_by"])
    op.create_index("ix_export_control_logs_timestamp", "export_control_logs", ["timestamp"])
    op.create_index("ix_export_control_logs_decision",  "export_control_logs", ["decision"])

    # ── super_admin_access_blocks ─────────────────────────────────────────────
    op.create_table(
        "super_admin_access_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("super_admin_id",    postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("super_admin_email", sa.String(255), nullable=True),
        sa.Column("organization_id",   postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("organization_name", sa.String(200), nullable=True),
        sa.Column("attempted_resource",sa.String(200), nullable=False),
        sa.Column("endpoint_path",     sa.String(500), nullable=True),
        sa.Column("http_method",       sa.String(10),  nullable=True),
        sa.Column("block_reason",      sa.Text,        nullable=False),
        sa.Column("ip_address",        sa.String(45),  nullable=True),
        sa.Column("timestamp",         sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sa_access_blocks_admin_id",  "super_admin_access_blocks", ["super_admin_id"])
    op.create_index("ix_sa_access_blocks_org_id",    "super_admin_access_blocks", ["organization_id"])
    op.create_index("ix_sa_access_blocks_timestamp", "super_admin_access_blocks", ["timestamp"])


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    op.drop_table("super_admin_access_blocks")
    op.drop_table("export_control_logs")
    op.drop_table("data_access_policies")
    op.drop_table("privacy_settings")

    _drop_enum("export_decision_enum", conn)
    _drop_enum("export_format_enum", conn)
    _drop_enum("export_scope_enum", conn)
    _drop_enum("chat_visibility_enum", conn)
