"""Phase 3 Routing Governance — routing_rules, routing_logs, prompt_categories, routing_feedback

Revision ID: g1h2i3j4k5l6
Revises: F1a2b3c4d5e6
Create Date: 2026-06-12

New tables:
    routing_rules       — admin-defined rules that override the smart router
    prompt_categories   — taxonomy of prompt categories for analytics
    routing_logs        — per-request routing decision audit trail
    routing_feedback    — user / admin feedback on routing quality
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "g1h2i3j4k5l6"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:

    # ── prompt_categories ────────────────────────────────────────────────────
    op.create_table(
        "prompt_categories",
        sa.Column("id",          sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name",        sa.String(100), nullable=False, unique=True),
        sa.Column("slug",        sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text,        nullable=True),
        sa.Column("color_hex",   sa.String(7),   nullable=True),   # UI badge colour e.g. "#3B82F6"
        sa.Column("is_active",   sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("created_at",  sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_prompt_categories_slug",      "prompt_categories", ["slug"])
    op.create_index("ix_prompt_categories_is_active", "prompt_categories", ["is_active"])

    # ── routing_rules ────────────────────────────────────────────────────────
    op.create_table(
        "routing_rules",
        sa.Column("id",              sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),   # NULL = platform-wide
        sa.Column("department_id",   sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("departments.id"),   nullable=True),
        sa.Column("name",            sa.String(200), nullable=False),
        sa.Column("description",     sa.Text,        nullable=True),
        # Match criteria (ANY non-null field is evaluated; all provided must match)
        sa.Column("match_category",  sa.String(100), nullable=True),   # prompt_categories.slug
        sa.Column("match_complexity",sa.String(20),  nullable=True),   # simple | medium | complex
        sa.Column("match_provider",  sa.String(100), nullable=True),   # current provider name
        sa.Column("match_model",     sa.String(200), nullable=True),
        sa.Column("cost_threshold_usd", sa.Numeric(10, 6), nullable=True),   # only fire if cost > X
        # Action
        sa.Column("action",          sa.String(50),  nullable=False),  # force_provider | force_model | block | fallback
        sa.Column("target_provider", sa.String(100), nullable=True),
        sa.Column("target_model",    sa.String(200), nullable=True),
        sa.Column("fallback_provider", sa.String(100), nullable=True),
        sa.Column("fallback_model",    sa.String(200), nullable=True),
        # Governance
        sa.Column("priority",        sa.Integer,     nullable=False, server_default="100"),  # lower = higher priority
        sa.Column("is_active",       sa.Boolean,     nullable=False, server_default="true"),
        sa.Column("created_by",      sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by",      sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at",      sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_routing_rules_org_id",     "routing_rules", ["organization_id"])
    op.create_index("ix_routing_rules_dept_id",    "routing_rules", ["department_id"])
    op.create_index("ix_routing_rules_priority",   "routing_rules", ["priority"])
    op.create_index("ix_routing_rules_is_active",  "routing_rules", ["is_active"])
    op.create_index("ix_routing_rules_action",     "routing_rules", ["action"])

    # ── routing_logs ─────────────────────────────────────────────────────────
    op.create_table(
        "routing_logs",
        sa.Column("id",              sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("user_id",         sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"),         nullable=True),
        sa.Column("conversation_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("usage_log_id",    sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("usage_logs.id"),    nullable=True),
        sa.Column("session_id",      sa.String(100),
                  sa.ForeignKey("sessions.session_id"), nullable=True),
        # Request snapshot
        sa.Column("prompt_preview",  sa.String(500), nullable=True),     # first 500 chars
        sa.Column("prompt_length",   sa.Integer,     nullable=True),
        sa.Column("category_slug",   sa.String(100), nullable=True),     # detected category
        sa.Column("complexity",      sa.String(20),  nullable=True),     # simple | medium | complex
        sa.Column("complexity_score",sa.Integer,     nullable=True),     # 0-100
        sa.Column("department",      sa.String(100), nullable=True),
        # Routing decision
        sa.Column("router_engine",   sa.String(50),  nullable=True),     # manual | ai | rule
        sa.Column("rule_id",         sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("routing_rules.id"), nullable=True),     # which rule fired, if any
        sa.Column("selected_provider",  sa.String(100), nullable=False),
        sa.Column("selected_model",     sa.String(200), nullable=True),
        sa.Column("was_fallback",       sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("fallback_reason",    sa.Text,        nullable=True),
        sa.Column("original_provider",  sa.String(100), nullable=True),  # before fallback
        sa.Column("original_model",     sa.String(200), nullable=True),
        # Cost & latency
        sa.Column("input_tokens",    sa.Integer,        nullable=True),
        sa.Column("output_tokens",   sa.Integer,        nullable=True),
        sa.Column("total_tokens",    sa.Integer,        nullable=True),
        sa.Column("cost_usd",        sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms",      sa.Integer,        nullable=True),
        sa.Column("status",          sa.String(20),     nullable=True),  # success | error | timeout
        sa.Column("error_message",   sa.Text,           nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_routing_logs_org_id",        "routing_logs", ["organization_id"])
    op.create_index("ix_routing_logs_user_id",       "routing_logs", ["user_id"])
    op.create_index("ix_routing_logs_provider",      "routing_logs", ["selected_provider"])
    op.create_index("ix_routing_logs_was_fallback",  "routing_logs", ["was_fallback"])
    op.create_index("ix_routing_logs_created_at",    "routing_logs", ["created_at"])
    op.create_index("ix_routing_logs_rule_id",       "routing_logs", ["rule_id"])
    op.create_index("ix_routing_logs_category",      "routing_logs", ["category_slug"])

    # ── routing_feedback ─────────────────────────────────────────────────────
    op.create_table(
        "routing_feedback",
        sa.Column("id",             sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("routing_log_id", sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("routing_logs.id"), nullable=False),
        sa.Column("organization_id",sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id"), nullable=True),
        sa.Column("submitted_by",   sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("feedback_type",  sa.String(50),  nullable=False),   # wrong_provider | wrong_model | unnecessary_fallback | too_expensive | quality_poor | correct
        sa.Column("rating",         sa.Integer,     nullable=True),    # 1-5
        sa.Column("suggested_provider", sa.String(100), nullable=True),
        sa.Column("suggested_model",    sa.String(200), nullable=True),
        sa.Column("notes",              sa.Text,        nullable=True),
        sa.Column("is_reviewed",        sa.Boolean,     nullable=False, server_default="false"),
        sa.Column("reviewed_by",        sa.dialects.postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("reviewed_at",  sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_routing_feedback_log_id",     "routing_feedback", ["routing_log_id"])
    op.create_index("ix_routing_feedback_org_id",     "routing_feedback", ["organization_id"])
    op.create_index("ix_routing_feedback_type",       "routing_feedback", ["feedback_type"])
    op.create_index("ix_routing_feedback_is_reviewed","routing_feedback", ["is_reviewed"])
    op.create_index("ix_routing_feedback_created_at", "routing_feedback", ["created_at"])


def downgrade() -> None:
    op.drop_table("routing_feedback")
    op.drop_table("routing_logs")
    op.drop_table("routing_rules")
    op.drop_table("prompt_categories")