"""k1l2m3n4o5p6_phase7_security_operations_center

Revision ID: k1l2m3n4o5p6
Revises: j1k2l3m4n5o6
Create Date: 2026-06-15

Phase 7 — Security Operations Center
Creates five new tables:
  login_history
  user_mfa
  api_tokens
  password_resets
  email_verifications

Also creates the ENUMs they depend on:
  login_event_type_enum
  mfa_status_enum
  mfa_method_enum
  api_token_status_enum

No existing tables are modified.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# ── Revision identifiers ──────────────────────────────────────────────────────
revision = "k1l2m3n4o5p6"
down_revision = "j1k2l3m4n5o6"
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
        "login_event_type_enum",
        (
            "success",
            "failed_password",
            "failed_mfa",
            "account_locked",
            "account_inactive",
            "invalid_token",
        ),
        conn,
    )
    _create_enum(
        "mfa_status_enum",
        ("disabled", "pending", "active"),
        conn,
    )
    _create_enum(
        "mfa_method_enum",
        ("totp", "backup_code", "email_otp", "sms_otp"),
        conn,
    )
    _create_enum(
        "api_token_status_enum",
        ("active", "revoked", "expired"),
        conn,
    )

    # ── login_history ─────────────────────────────────────────────────────────
    op.create_table(
        "login_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("email_attempted", sa.String(255), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum(
                "success", "failed_password", "failed_mfa",
                "account_locked", "account_inactive", "invalid_token",
                name="login_event_type_enum",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("ip_address",           sa.String(45),   nullable=True),
        sa.Column("user_agent",           sa.Text,         nullable=True),
        sa.Column("geo_country",          sa.String(2),    nullable=True),
        sa.Column("geo_city",             sa.String(100),  nullable=True),
        sa.Column("auth_method",          sa.String(50),   nullable=True),
        sa.Column("mfa_used",             sa.Boolean,      nullable=False, server_default="false"),
        sa.Column("mfa_method",           sa.String(20),   nullable=True),
        sa.Column("failure_reason",       sa.Text,         nullable=True),
        sa.Column("consecutive_failures", sa.Integer,      nullable=False, server_default="0"),
        sa.Column("session_token_jti",    sa.String(64),   nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_login_history_user_id",    "login_history", ["user_id"])
    op.create_index("ix_login_history_event_type", "login_history", ["event_type"])
    op.create_index("ix_login_history_ip_address", "login_history", ["ip_address"])
    op.create_index("ix_login_history_created_at", "login_history", ["created_at"])

    # ── user_mfa ──────────────────────────────────────────────────────────────
    op.create_table(
        "user_mfa",
        sa.Column("id",      postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column(
            "status",
            sa.Enum("disabled", "pending", "active",
                    name="mfa_status_enum", create_type=False),
            nullable=False,
            server_default="disabled",
        ),
        sa.Column(
            "method",
            sa.Enum("totp", "backup_code", "email_otp", "sms_otp",
                    name="mfa_method_enum", create_type=False),
            nullable=True,
        ),
        sa.Column("totp_secret_enc",       sa.Text,    nullable=True),
        sa.Column("totp_confirmed_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("backup_codes_hashed",   sa.JSON,    nullable=False, server_default="'[]'"),
        sa.Column("backup_codes_used",     sa.Integer, nullable=False, server_default="0"),
        sa.Column("otp_phone_enc",         sa.String(255), nullable=True),
        sa.Column("otp_email",             sa.String(255), nullable=True),
        sa.Column("grace_period_ends_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("enforcement_required",  sa.Boolean, nullable=False, server_default="false"),
        sa.Column("enrolled_at",           sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at",           sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_by",           postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_user_mfa_user_id", "user_mfa", ["user_id"])
    op.create_index("ix_user_mfa_status",  "user_mfa", ["status"])

    # ── api_tokens ────────────────────────────────────────────────────────────
    op.create_table(
        "api_tokens",
        sa.Column("id",      postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name",         sa.String(200), nullable=False),
        sa.Column("description",  sa.Text,        nullable=True),
        sa.Column("token_prefix", sa.String(12),  nullable=False),
        sa.Column("token_hash",   sa.String(64),  nullable=False, unique=True),
        sa.Column("scopes",       sa.JSON,        nullable=False, server_default="'[]'"),
        sa.Column(
            "status",
            sa.Enum("active", "revoked", "expired",
                    name="api_token_status_enum", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column("expires_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at",   sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_ip",   sa.String(45),  nullable=True),
        sa.Column("request_count",  sa.Integer,     nullable=False, server_default="0"),
        sa.Column("revoked_at",     sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by",     postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("revoke_reason",  sa.Text,        nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_api_tokens_user_id",    "api_tokens", ["user_id"])
    op.create_index("ix_api_tokens_token_hash", "api_tokens", ["token_hash"])
    op.create_index("ix_api_tokens_status",     "api_tokens", ["status"])

    # ── password_resets ───────────────────────────────────────────────────────
    op.create_table(
        "password_resets",
        sa.Column("id",      postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash",      sa.String(64),  nullable=False, unique=True),
        sa.Column("expires_at",      sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_used",         sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("used_at",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_ip",         sa.String(45),  nullable=True),
        sa.Column("requested_ip",    sa.String(45),  nullable=True),
        sa.Column("requested_email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_password_resets_user_id",    "password_resets", ["user_id"])
    op.create_index("ix_password_resets_token_hash", "password_resets", ["token_hash"])
    op.create_index("ix_password_resets_is_used",    "password_resets", ["is_used"])

    # ── email_verifications ───────────────────────────────────────────────────
    op.create_table(
        "email_verifications",
        sa.Column("id",      postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash",         sa.String(64),  nullable=False, unique=True),
        sa.Column("expires_at",         sa.DateTime(timezone=True), nullable=False),
        sa.Column("verification_type",  sa.String(30),  nullable=False, server_default="registration"),
        sa.Column("email_to_verify",    sa.String(255), nullable=False),
        sa.Column("previous_email",     sa.String(255), nullable=True),
        sa.Column("is_used",  sa.Boolean, nullable=False, server_default="false"),
        sa.Column("used_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_ip",  sa.String(45), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_email_verifications_user_id",    "email_verifications", ["user_id"])
    op.create_index("ix_email_verifications_token_hash", "email_verifications", ["token_hash"])
    op.create_index("ix_email_verifications_is_used",    "email_verifications", ["is_used"])


# ── downgrade ─────────────────────────────────────────────────────────────────

def downgrade() -> None:
    conn = op.get_bind()

    op.drop_table("email_verifications")
    op.drop_table("password_resets")
    op.drop_table("api_tokens")
    op.drop_table("user_mfa")
    op.drop_table("login_history")

    _drop_enum("api_token_status_enum", conn)
    _drop_enum("mfa_method_enum",       conn)
    _drop_enum("mfa_status_enum",       conn)
    _drop_enum("login_event_type_enum", conn)
