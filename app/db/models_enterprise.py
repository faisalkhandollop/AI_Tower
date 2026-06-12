"""
app/db/models_enterprise.py
============================
Phase 1 Enterprise Models — added on top of existing models.py.
Does NOT modify any existing table.

New tables:
  roles                   — system role definitions
  permissions             — fine-grained permission definitions
  role_permissions        — M2M: role ↔ permission
  user_roles              — M2M: user ↔ role (org-scoped)
  conversations           — chat conversation threads
  messages                — individual messages within conversations
  attachments             — files attached to messages
  audit_logs              — immutable audit trail of all actions
  privileged_access_logs  — admin/support access to customer data
  encryption_keys         — AES-256 key metadata (actual keys in env/KMS)
"""

import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey,
    Index, Integer, Numeric, JSON, String, Text,
    UniqueConstraint, Enum as SAEnum
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# ROLES
# Seeded once at startup. Never created by end users.
# ─────────────────────────────────────────────────────────────
ROLE_NAMES = (
    "SUPER_ADMIN",
    "ORG_ADMIN",
    "DEPARTMENT_MANAGER",
    "TEAM_LEAD",
    "USER",
)

class Role(Base):
    __tablename__ = "roles"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(SAEnum(*ROLE_NAMES, name="role_name_enum"), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# PERMISSIONS
# Granular action-level permissions.
# ─────────────────────────────────────────────────────────────
PERMISSION_NAMES = (
    # User management
    "view_users",
    "create_users",
    "update_users",
    "delete_users",
    # Cost & usage
    "view_costs",
    "view_usage",
    "export_reports",
    # API keys
    "manage_api_keys",
    "view_api_keys",
    # Conversations (org-owned, super_admin explicitly excluded)
    "view_conversations",
    "delete_conversations",
    # Organization
    "manage_departments",
    "manage_teams",
    "manage_org_settings",
    # Platform (super_admin only)
    "manage_organizations",
    "view_platform_metrics",
    "manage_providers",
    "manage_routing",
)

class Permission(Base):
    __tablename__ = "permissions"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(SAEnum(*PERMISSION_NAMES, name="permission_name_enum"), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# ROLE PERMISSIONS  (M2M)
# ─────────────────────────────────────────────────────────────
class RolePermission(Base):
    __tablename__ = "role_permissions"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_id       = Column(UUID(as_uuid=True), ForeignKey("roles.id"),       nullable=False)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permission"),
        Index("ix_role_permissions_role_id", "role_id"),
    )


# ─────────────────────────────────────────────────────────────
# USER ROLES  (M2M, org-scoped)
# A user can have different roles in different organizations.
# e.g. ORG_ADMIN in Dollop, USER in TechCorp.
# ─────────────────────────────────────────────────────────────
class UserOrgRole(Base):
    __tablename__ = "user_org_roles"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=False)
    role_id         = Column(UUID(as_uuid=True), ForeignKey("roles.id"),         nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    assigned_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=True)
    assigned_at     = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "role_id", "organization_id", name="uq_user_org_role"),
        Index("ix_user_org_roles_user_id",   "user_id"),
        Index("ix_user_org_roles_org_id",    "organization_id"),
    )


# ─────────────────────────────────────────────────────────────
# CONVERSATIONS
# A conversation thread — linked to a user and org.
# Replaces bare session references for structured message history.
# Sessions still exist (from existing code) — conversations link to them.
# ─────────────────────────────────────────────────────────────
class Conversation(Base):
    __tablename__ = "conversations"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=False)
    session_id      = Column(String(100), ForeignKey("sessions.session_id"),     nullable=True)   # link to existing sessions
    title           = Column(String(500), nullable=True)
    department      = Column(String(100), nullable=True)
    team_id         = Column(UUID(as_uuid=True), ForeignKey("teams.id"),         nullable=True)
    is_archived     = Column(Boolean, nullable=False, default=False)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_conversations_org_id",    "organization_id"),
        Index("ix_conversations_user_id",   "user_id"),
        Index("ix_conversations_created_at","created_at"),
    )


# ─────────────────────────────────────────────────────────────
# MESSAGES
# Individual turn within a conversation.
# role: "user" | "assistant" | "system"
# ─────────────────────────────────────────────────────────────
class Message(Base):
    __tablename__ = "messages"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"),  nullable=False)
    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id"),  nullable=False)
    role            = Column(String(20),  nullable=False)   # user | assistant | system
    content         = Column(Text,        nullable=False)
    token_count     = Column(Integer,     nullable=True)
    provider        = Column(String(100), nullable=True)    # which AI provider responded
    model           = Column(String(100), nullable=True)
    cost            = Column(Numeric(10, 6), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
        Index("ix_messages_org_id",          "organization_id"),
        Index("ix_messages_created_at",      "created_at"),
    )


# ─────────────────────────────────────────────────────────────
# ATTACHMENTS
# Files attached to messages.
# ─────────────────────────────────────────────────────────────
class Attachment(Base):
    __tablename__ = "attachments"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    message_id      = Column(UUID(as_uuid=True), ForeignKey("messages.id"),      nullable=False)
    uploaded_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=False)
    filename        = Column(String(500), nullable=False)
    storage_path    = Column(Text, nullable=False)   # MinIO object path
    mime_type       = Column(String(200), nullable=True)
    file_size       = Column(Integer, nullable=True)   # bytes
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_attachments_org_id",    "organization_id"),
        Index("ix_attachments_message_id","message_id"),
    )


# ─────────────────────────────────────────────────────────────
# AUDIT LOGS
# Immutable. Never updated or deleted.
# Written automatically by the audit middleware on every state-changing action.
# ─────────────────────────────────────────────────────────────
AUDIT_ACTIONS = (
    "USER_CREATED",
    "USER_UPDATED",
    "USER_DEACTIVATED",
    "USER_DELETED",
    "ROLE_ASSIGNED",
    "ROLE_REVOKED",
    "ROLE_CHANGED",
    "LOGIN_SUCCESS",
    "LOGIN_FAILED",
    "LOGOUT",
    "PASSWORD_CHANGED",
    "ORG_CREATED",
    "ORG_UPDATED",
    "ORG_SUSPENDED",
    "ORG_DELETED",
    "TEAM_CREATED",
    "TEAM_UPDATED",
    "TEAM_DELETED",
    "DEPARTMENT_CREATED",
    "DEPARTMENT_UPDATED",
    "DEPARTMENT_DELETED",
    "MEMBER_ADDED",
    "MEMBER_REMOVED",
    "API_KEY_ADDED",
    "API_KEY_ROTATED",
    "API_KEY_DELETED",
    "FILE_UPLOADED",
    "FILE_DELETED",
    "CONVERSATION_DELETED",
    "QUOTA_CHANGED",
    "BUDGET_CHANGED",
    "REPORT_GENERATED",
    "ALERT_TRIGGERED",
)

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    actor_id        = Column(UUID(as_uuid=True), ForeignKey("users.id"),         nullable=True)
    actor_email     = Column(String(255), nullable=True)   # denormalized for immutability
    actor_role      = Column(String(50),  nullable=True)   # denormalized
    action          = Column(SAEnum(*AUDIT_ACTIONS, name="audit_action_enum"), nullable=False)
    resource_type   = Column(String(100), nullable=True)   # "user" | "team" | "api_key" etc.
    resource_id     = Column(String(100), nullable=True)   # UUID of the affected resource
    resource_name   = Column(String(500), nullable=True)   # human-readable name
    detail          = Column(Text, nullable=True)          # JSON string of changed fields
    ip_address      = Column(String(45),  nullable=True)   # IPv4 or IPv6
    user_agent      = Column(String(500), nullable=True)
    timestamp       = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_audit_logs_org_id",    "organization_id"),
        Index("ix_audit_logs_actor_id",  "actor_id"),
        Index("ix_audit_logs_action",    "action"),
        Index("ix_audit_logs_timestamp", "timestamp"),
    )


# ─────────────────────────────────────────────────────────────
# PRIVILEGED ACCESS LOGS
# Tracks every time a support/admin person accesses customer data.
# Required for compliance (SOC2, ISO27001).
# ─────────────────────────────────────────────────────────────
class PrivilegedAccessLog(Base):
    __tablename__ = "privileged_access_logs"

    id                  = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    employee_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    employee_name       = Column(String(200), nullable=False)
    employee_email      = Column(String(255), nullable=False)
    role                = Column(String(100), nullable=False)
    organization_id     = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=True)
    organization_name   = Column(String(200), nullable=True)   # denormalized
    resource_accessed   = Column(String(200), nullable=False)  # e.g. "users", "audit_logs", "usage_logs"
    reason              = Column(Text, nullable=False)         # required justification
    ip_address          = Column(String(45),  nullable=True)
    access_granted      = Column(Boolean, nullable=False, default=True)
    timestamp           = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_priv_access_employee_id",  "employee_id"),
        Index("ix_priv_access_org_id",       "organization_id"),
        Index("ix_priv_access_timestamp",    "timestamp"),
    )


# ── APPEND THIS TO THE BOTTOM OF app/db/models_enterprise.py ──────────────

# =============================================================================
# SUBSCRIPTION MANAGEMENT — Phase 1 SaaS
# =============================================================================

PLAN_TIERS = ("free", "pro", "enterprise")

SUBSCRIPTION_STATUSES = (
    "active",
    "trialing",
    "past_due",
    "suspended",
    "cancelled",
    "expired",
)


# ─────────────────────────────────────────────────────────────
# SUBSCRIPTION PLANS
# Defined by Super Admin. Org admins cannot create plans.
# ─────────────────────────────────────────────────────────────
class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name                  = Column(String(100), nullable=False, unique=True)   # "Free", "Pro", "Enterprise"
    tier                  = Column(SAEnum(*PLAN_TIERS, name="plan_tier_enum"), nullable=False)
    description           = Column(Text, nullable=True)
    price_monthly_usd     = Column(Numeric(10, 2), nullable=False, default=0)
    price_yearly_usd      = Column(Numeric(10, 2), nullable=True)              # yearly discount

    # Hard limits enforced at runtime
    max_users             = Column(Integer, nullable=True)                     # None = unlimited
    max_teams             = Column(Integer, nullable=True)
    max_departments       = Column(Integer, nullable=True)
    monthly_token_quota   = Column(Integer, nullable=True)                     # None = unlimited
    monthly_request_quota = Column(Integer, nullable=True)
    max_api_keys          = Column(Integer, nullable=True)

    # Feature flags
    allowed_providers     = Column(JSON, nullable=False, default=list)         # ["groq","openai",...]
    features              = Column(JSON, nullable=False, default=dict)         # {"kb": true, "audit": true, ...}

    is_active             = Column(Boolean, nullable=False, default=True)
    is_public             = Column(Boolean, nullable=False, default=True)      # visible in pricing UI
    created_at            = Column(DateTime(timezone=True), server_default=func.now())
    updated_at            = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_subscription_plans_tier",      "tier"),
        Index("ix_subscription_plans_is_active", "is_active"),
    )


# ─────────────────────────────────────────────────────────────
# SUBSCRIPTIONS
# One active subscription per organization.
# ─────────────────────────────────────────────────────────────
class Subscription(Base):
    __tablename__ = "subscriptions"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False, unique=True)
    plan_id         = Column(UUID(as_uuid=True), ForeignKey("subscription_plans.id"), nullable=False)

    status          = Column(
        SAEnum(*SUBSCRIPTION_STATUSES, name="subscription_status_enum"),
        nullable=False,
        default="active",
    )

    # Billing period
    current_period_start = Column(DateTime(timezone=True), nullable=False)
    current_period_end   = Column(DateTime(timezone=True), nullable=False)

    # Usage limits for THIS billing cycle (copied from plan at subscription time,
    # allows custom overrides per org without touching the plan)
    token_quota_override   = Column(Integer, nullable=True)    # None = use plan default
    request_quota_override = Column(Integer, nullable=True)
    user_limit_override    = Column(Integer, nullable=True)

    # Lifecycle tracking
    trial_ends_at          = Column(DateTime(timezone=True), nullable=True)
    cancelled_at           = Column(DateTime(timezone=True), nullable=True)
    suspended_at           = Column(DateTime(timezone=True), nullable=True)
    suspension_reason      = Column(Text, nullable=True)

    # Who managed it last
    assigned_by            = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    notes                  = Column(Text, nullable=True)        # internal Super Admin notes

    created_at             = Column(DateTime(timezone=True), server_default=func.now())
    updated_at             = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_subscriptions_org_id",    "organization_id"),
        Index("ix_subscriptions_plan_id",   "plan_id"),
        Index("ix_subscriptions_status",    "status"),
        Index("ix_subscriptions_period_end","current_period_end"),
    )