"""
app/db/models_routing.py
========================
Phase 3 Routing Governance Models.

New tables:
    prompt_categories  — taxonomy for analytics labelling
    routing_rules      — admin override rules evaluated before the smart router
    routing_logs       — per-request routing decision audit trail
    routing_feedback   — quality feedback on routing decisions
"""

import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Index, Integer, Numeric, String, Text,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# PROMPT CATEGORIES
# Seeded with sensible defaults; org admins may add their own.
# ─────────────────────────────────────────────────────────────
DEFAULT_CATEGORIES = (
    "code_generation",
    "code_review",
    "data_analysis",
    "document_summary",
    "creative_writing",
    "translation",
    "question_answering",
    "reasoning",
    "image_understanding",
    "general",
)


class PromptCategory(Base):
    __tablename__ = "prompt_categories"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(String(100), nullable=False, unique=True)
    slug        = Column(String(100), nullable=False, unique=True)
    description = Column(Text,        nullable=True)
    color_hex   = Column(String(7),   nullable=True)     # e.g. "#3B82F6"
    is_active   = Column(Boolean,     nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_prompt_categories_slug",      "slug"),
        Index("ix_prompt_categories_is_active", "is_active"),
    )


# ─────────────────────────────────────────────────────────────
# ROUTING RULES
# Evaluated in ascending priority order before the smart router.
# First matching active rule wins.
# ─────────────────────────────────────────────────────────────
ROUTING_ACTIONS = (
    "force_provider",   # send to target_provider / target_model
    "force_model",      # keep current provider, switch model
    "block",            # reject this request with a reason
    "fallback",         # override fallback provider/model
    "cost_cap",         # if projected cost > threshold, downgrade to target
)


class RoutingRule(Base):
    __tablename__ = "routing_rules"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)  # NULL = platform-wide
    department_id   = Column(UUID(as_uuid=True), ForeignKey("departments.id"),   nullable=True)

    name            = Column(String(200), nullable=False)
    description     = Column(Text,        nullable=True)

    # ── Match criteria ────────────────────────────────────────
    match_category   = Column(String(100), nullable=True)     # prompt_categories.slug
    match_complexity = Column(String(20),  nullable=True)     # simple | medium | complex
    match_provider   = Column(String(100), nullable=True)     # current provider
    match_model      = Column(String(200), nullable=True)
    cost_threshold_usd = Column(Numeric(10, 6), nullable=True)

    # ── Action ────────────────────────────────────────────────
    action           = Column(SAEnum(*ROUTING_ACTIONS, name="routing_action_enum"), nullable=False)
    target_provider  = Column(String(100), nullable=True)
    target_model     = Column(String(200), nullable=True)
    fallback_provider= Column(String(100), nullable=True)
    fallback_model   = Column(String(200), nullable=True)

    # ── Governance ────────────────────────────────────────────
    priority         = Column(Integer,  nullable=False, default=100)   # lower = higher priority
    is_active        = Column(Boolean,  nullable=False, default=True)
    created_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_routing_rules_org_id",    "organization_id"),
        Index("ix_routing_rules_dept_id",   "department_id"),
        Index("ix_routing_rules_priority",  "priority"),
        Index("ix_routing_rules_is_active", "is_active"),
        Index("ix_routing_rules_action",    "action"),
    )


# ─────────────────────────────────────────────────────────────
# ROUTING LOGS
# One row per AI request — immutable audit trail of every
# routing decision made by the platform.
# ─────────────────────────────────────────────────────────────
class RoutingLog(Base):
    __tablename__ = "routing_logs"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=True)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True)
    usage_log_id    = Column(UUID(as_uuid=True), ForeignKey("usage_logs.id"),    nullable=True)
    session_id      = Column(String(100),        ForeignKey("sessions.session_id"), nullable=True)

    # Request snapshot
    prompt_preview   = Column(String(500), nullable=True)
    prompt_length    = Column(Integer,     nullable=True)
    category_slug    = Column(String(100), nullable=True)
    complexity       = Column(String(20),  nullable=True)
    complexity_score = Column(Integer,     nullable=True)
    department       = Column(String(100), nullable=True)

    # Routing decision
    router_engine    = Column(String(50),  nullable=True)          # manual | ai | rule
    rule_id          = Column(UUID(as_uuid=True), ForeignKey("routing_rules.id"), nullable=True)
    selected_provider= Column(String(100), nullable=False)
    selected_model   = Column(String(200), nullable=True)
    was_fallback     = Column(Boolean,     nullable=False, default=False)
    fallback_reason  = Column(Text,        nullable=True)
    original_provider= Column(String(100), nullable=True)
    original_model   = Column(String(200), nullable=True)

    # Outcome
    input_tokens     = Column(Integer,        nullable=True)
    output_tokens    = Column(Integer,        nullable=True)
    total_tokens     = Column(Integer,        nullable=True)
    cost_usd         = Column(Numeric(10, 6), nullable=True)
    latency_ms       = Column(Integer,        nullable=True)
    status           = Column(String(20),     nullable=True)       # success | error | timeout
    error_message    = Column(Text,           nullable=True)

    created_at       = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_routing_logs_org_id",       "organization_id"),
        Index("ix_routing_logs_user_id",      "user_id"),
        Index("ix_routing_logs_provider",     "selected_provider"),
        Index("ix_routing_logs_was_fallback", "was_fallback"),
        Index("ix_routing_logs_created_at",   "created_at"),
        Index("ix_routing_logs_rule_id",      "rule_id"),
        Index("ix_routing_logs_category",     "category_slug"),
    )


# ─────────────────────────────────────────────────────────────
# ROUTING FEEDBACK
# Users or admins flag bad routing decisions for review.
# Feeds back into rule improvement.
# ─────────────────────────────────────────────────────────────
FEEDBACK_TYPES = (
    "wrong_provider",
    "wrong_model",
    "unnecessary_fallback",
    "too_expensive",
    "quality_poor",
    "too_slow",
    "correct",
)


class RoutingFeedback(Base):
    __tablename__ = "routing_feedback"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    routing_log_id  = Column(UUID(as_uuid=True), ForeignKey("routing_logs.id"),  nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    submitted_by    = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=True)

    feedback_type    = Column(SAEnum(*FEEDBACK_TYPES, name="routing_feedback_type_enum"), nullable=False)
    rating           = Column(Integer,     nullable=True)       # 1–5
    suggested_provider = Column(String(100), nullable=True)
    suggested_model    = Column(String(200), nullable=True)
    notes              = Column(Text,        nullable=True)

    is_reviewed      = Column(Boolean,  nullable=False, default=False)
    reviewed_by      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at      = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_routing_feedback_log_id",      "routing_log_id"),
        Index("ix_routing_feedback_org_id",      "organization_id"),
        Index("ix_routing_feedback_type",        "feedback_type"),
        Index("ix_routing_feedback_is_reviewed", "is_reviewed"),
        Index("ix_routing_feedback_created_at",  "created_at"),
    )
