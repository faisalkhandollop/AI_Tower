"""
app/db/models_privacy.py
=========================
Phase 5 — Privacy Enforcement Models.

New tables:
  privacy_settings         — per-org privacy configuration (chat visibility, exports, data access)
  data_access_policies     — named policies that govern what roles can access which data types
  export_controls          — records of every data export attempt (allowed or blocked)
  super_admin_access_blocks — audit trail of blocked SUPER_ADMIN attempts to access org content

Architecture notes:
  - SUPER_ADMIN is blocked at the service layer from accessing prompts / responses /
    files / knowledge bases. These models store configuration + event records.
  - privacy_settings is 1:1 with organizations. Created with sane defaults when
    an org is provisioned; org admin can tighten but never loosen beyond defaults.
  - All tables are append-only or update-by-replace — no soft-delete.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────

CHAT_VISIBILITY_OPTIONS = (
    "org_admin_only",       # only ORG_ADMIN can view conversations
    "manager_and_above",    # DEPARTMENT_MANAGER, ORG_ADMIN can view dept conversations
    "team_lead_and_above",  # TEAM_LEAD, DEPARTMENT_MANAGER, ORG_ADMIN
    "none",                 # nobody can view — fully private (user-only access)
)

EXPORT_CONTROL_SCOPES = (
    "conversations",
    "messages",
    "usage_logs",
    "audit_logs",
    "user_list",
    "api_keys",
    "attachments",
    "knowledge_base",
)

EXPORT_FORMATS = (
    "json",
    "csv",
    "pdf",
)

ACCESS_DECISION = (
    "allowed",
    "blocked",
)

DATA_CATEGORIES = (
    "conversations",       # chat threads + titles
    "messages",            # prompt content + AI responses
    "attachments",         # uploaded files
    "knowledge_base",      # KB documents + embeddings
    "api_keys",            # provider API keys (even encrypted)
    "usage_logs_detail",   # logs with prompt/response columns
    "user_pii",            # email, name columns
)


# ─────────────────────────────────────────────────────────────
# PRIVACY SETTINGS
# One row per organization. Controls what is visible to which roles
# within that organization AND enforces the super_admin blackout.
# ─────────────────────────────────────────────────────────────
class PrivacySettings(Base):
    __tablename__ = "privacy_settings"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id"),
        nullable=False,
        unique=True,
    )

    # ── Chat visibility ────────────────────────────────────────
    # Who *within the org* can view other users' conversations.
    chat_visibility = Column(
        SAEnum(*CHAT_VISIBILITY_OPTIONS, name="chat_visibility_enum"),
        nullable=False,
        default="org_admin_only",
    )

    # ── Super admin blackout (platform-level, not org-configurable) ──
    # These are always True — stored here so enforcement code can assert
    # the invariant and so audit queries have a definitive source of truth.
    block_super_admin_conversations = Column(Boolean, nullable=False, default=True)
    block_super_admin_messages      = Column(Boolean, nullable=False, default=True)
    block_super_admin_attachments   = Column(Boolean, nullable=False, default=True)
    block_super_admin_kb            = Column(Boolean, nullable=False, default=True)
    block_super_admin_api_keys      = Column(Boolean, nullable=False, default=True)

    # ── Export controls ────────────────────────────────────────
    exports_enabled             = Column(Boolean, nullable=False, default=True)
    # JSON list of EXPORT_CONTROL_SCOPES that are permitted
    allowed_export_scopes       = Column(
        JSON, nullable=False,
        default=lambda: ["usage_logs", "audit_logs", "user_list"],
    )
    # Require a written reason before any export is allowed
    export_requires_reason      = Column(Boolean, nullable=False, default=True)
    # Notify org admin by email when an export is performed
    export_notify_org_admin     = Column(Boolean, nullable=False, default=True)

    # ── Data retention ─────────────────────────────────────────
    # 0 = keep forever; positive integer = days before auto-purge
    message_retention_days      = Column(Integer, nullable=False, default=0)
    attachment_retention_days   = Column(Integer, nullable=False, default=0)
    usage_log_retention_days    = Column(Integer, nullable=False, default=365)

    # ── Misc ───────────────────────────────────────────────────
    last_updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_privacy_settings_org_id", "organization_id"),
    )


# ─────────────────────────────────────────────────────────────
# DATA ACCESS POLICIES
# Named, org-scoped policies that map roles to permitted data categories.
# An org admin can create multiple policies and assign them to teams/depts.
# The platform enforces: SUPER_ADMIN is *never* in any policy's allowed_roles
# for customer data categories (enforced at creation time in the service layer).
# ─────────────────────────────────────────────────────────────
class DataAccessPolicy(Base):
    __tablename__ = "data_access_policies"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name            = Column(String(200), nullable=False)
    description     = Column(Text, nullable=True)

    # JSON list of DATA_CATEGORIES this policy permits access to
    permitted_data_categories = Column(JSON, nullable=False, default=list)

    # JSON list of role names (from ROLE_NAMES) that this policy applies to.
    # SUPER_ADMIN must never appear here for customer-data categories.
    allowed_roles = Column(JSON, nullable=False, default=list)

    is_active       = Column(Boolean, nullable=False, default=True)
    created_by      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_data_access_policy_name"),
        Index("ix_data_access_policies_org_id", "organization_id"),
    )


# ─────────────────────────────────────────────────────────────
# EXPORT CONTROLS
# Every export attempt is logged here — whether allowed or blocked.
# Used for compliance reporting and anomaly detection.
# ─────────────────────────────────────────────────────────────
class ExportControlLog(Base):
    __tablename__ = "export_control_logs"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    requested_by    = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=False)
    requester_role  = Column(String(50),  nullable=False)   # denormalized
    requester_email = Column(String(255), nullable=True)    # denormalized

    scope           = Column(
        SAEnum(*EXPORT_CONTROL_SCOPES, name="export_scope_enum"),
        nullable=False,
    )
    export_format   = Column(
        SAEnum(*EXPORT_FORMATS, name="export_format_enum"),
        nullable=False,
        default="json",
    )
    filters_applied = Column(JSON, nullable=True)    # e.g. {"date_from": "...", "user_id": "..."}
    reason          = Column(Text, nullable=True)    # required when export_requires_reason=True

    decision        = Column(
        SAEnum(*ACCESS_DECISION, name="export_decision_enum"),
        nullable=False,
    )
    block_reason    = Column(Text, nullable=True)    # set when decision == "blocked"

    record_count    = Column(Integer, nullable=True) # how many rows were exported (if allowed)
    ip_address      = Column(String(45), nullable=True)
    timestamp       = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_export_control_logs_org_id",    "organization_id"),
        Index("ix_export_control_logs_requester",  "requested_by"),
        Index("ix_export_control_logs_timestamp",  "timestamp"),
        Index("ix_export_control_logs_decision",   "decision"),
    )


# ─────────────────────────────────────────────────────────────
# SUPER ADMIN ACCESS BLOCKS
# Every time the privacy enforcement layer blocks a SUPER_ADMIN from
# accessing customer content, a row is written here.
# Separate from export_control_logs because these are enforcement events,
# not user-initiated exports.
# ─────────────────────────────────────────────────────────────
class SuperAdminAccessBlock(Base):
    __tablename__ = "super_admin_access_blocks"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    super_admin_id      = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=True)
    super_admin_email   = Column(String(255), nullable=True)   # denormalized
    organization_id     = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    organization_name   = Column(String(200), nullable=True)   # denormalized

    # What endpoint / data type was attempted
    attempted_resource  = Column(String(200), nullable=False)  # e.g. "conversations", "messages"
    endpoint_path       = Column(String(500), nullable=True)   # HTTP path
    http_method         = Column(String(10),  nullable=True)   # GET | POST | DELETE

    block_reason        = Column(Text, nullable=False)         # why blocked (policy name)
    ip_address          = Column(String(45),  nullable=True)
    timestamp           = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_sa_access_blocks_admin_id",   "super_admin_id"),
        Index("ix_sa_access_blocks_org_id",     "organization_id"),
        Index("ix_sa_access_blocks_timestamp",  "timestamp"),
    )