"""
app/db/models_support.py
==========================
Phase 6 — Support Access Workflow Models.

New tables:
  support_access_requests — customer-raised tickets requesting temporary
                             support engineer access to their org data
  support_access_logs      — immutable audit trail of every action taken
                             against a support access request, plus every
                             access made under a granted scoped token

Architecture notes:
  - A support access request starts in the customer's org (raised by an
    ORG_ADMIN / authorized user) and must be approved by an internal
    platform admin (SUPER_ADMIN) before any access is granted.
  - Approval mints a short-lived, scope-limited JWT ("access grant token")
    that the support engineer uses to access only the resources listed in
    `granted_scopes`, only within the org named in `organization_id`, and
    only until `expires_at`.
  - SUPER_ADMIN's normal blackout from customer content (Phase 5) is
    explicitly bypassed ONLY for an active, approved, non-expired,
    non-revoked support access grant — and only for the scopes it lists.
  - Every lifecycle transition (requested → approved/denied → revoked/
    expired) and every resource access performed under a grant is written
    to support_access_logs. This table is append-only.
"""

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────

SUPPORT_ACCESS_STATUSES = (
    "pending",    # raised by customer, awaiting admin approval
    "approved",   # approved — access grant active until expires_at
    "denied",     # admin rejected the request
    "revoked",    # access manually revoked before expiry
    "expired",    # access window elapsed naturally
)

# Scopes mirror the customer-data categories a support engineer may be
# granted read access to. Deliberately narrow and enumerable — never "all".
SUPPORT_ACCESS_SCOPES = (
    "conversations",
    "messages",
    "usage_logs",
    "audit_logs",
    "user_list",
    "billing",
    "attachments",
    "knowledge_base",
)

SUPPORT_ACCESS_LOG_ACTIONS = (
    "REQUEST_CREATED",
    "REQUEST_APPROVED",
    "REQUEST_DENIED",
    "ACCESS_GRANTED",
    "ACCESS_REVOKED",
    "ACCESS_EXPIRED",
    "RESOURCE_ACCESSED",
)


# ─────────────────────────────────────────────────────────────
# SUPPORT ACCESS REQUESTS
# One row per support ticket / access request lifecycle.
# ─────────────────────────────────────────────────────────────
class SupportAccessRequest(Base):
    __tablename__ = "support_access_requests"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)

    # ── Ticket context ──────────────────────────────────────────
    ticket_reference = Column(String(200), nullable=True)   # external helpdesk ticket id
    reason           = Column(Text, nullable=False)         # why access is needed (required)

    # ── Requester (customer side) ──────────────────────────────
    requested_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    requester_email    = Column(String(255), nullable=True)   # denormalized
    requester_role     = Column(String(50),  nullable=True)   # denormalized

    # ── Requested scope & duration ──────────────────────────────
    requested_scopes  = Column(JSON, nullable=False, default=list)  # list[str] from SUPPORT_ACCESS_SCOPES
    requested_minutes = Column(JSON, nullable=False, default=lambda: 60)  # int, stored as JSON for portability

    # ── Status ────────────────────────────────────────────────
    status = Column(
        SAEnum(*SUPPORT_ACCESS_STATUSES, name="support_access_status_enum"),
        nullable=False,
        default="pending",
    )

    # ── Approval (platform side) ─────────────────────────────────
    reviewed_by       = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewer_email    = Column(String(255), nullable=True)   # denormalized
    review_notes      = Column(Text, nullable=True)
    reviewed_at       = Column(DateTime(timezone=True), nullable=True)

    # ── Granted access (set on approval) ──────────────────────────
    granted_scopes  = Column(JSON, nullable=True)   # list[str], subset of requested_scopes
    granted_to      = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # support engineer
    grantee_email   = Column(String(255), nullable=True)   # denormalized
    access_token_jti = Column(String(64), nullable=True, unique=True)  # JWT "jti" claim of the grant token
    expires_at       = Column(DateTime(timezone=True), nullable=True)

    # ── Revocation ────────────────────────────────────────────
    revoked_by   = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    revoked_at   = Column(DateTime(timezone=True), nullable=True)
    revoke_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_support_access_requests_org_id", "organization_id"),
        Index("ix_support_access_requests_status", "status"),
        Index("ix_support_access_requests_requested_by", "requested_by"),
        Index("ix_support_access_requests_granted_to", "granted_to"),
        Index("ix_support_access_requests_jti", "access_token_jti"),
    )


# ─────────────────────────────────────────────────────────────
# SUPPORT ACCESS LOGS
# Immutable, append-only audit trail of every state transition and
# every resource access performed under a support access grant.
# ─────────────────────────────────────────────────────────────
class SupportAccessLog(Base):
    __tablename__ = "support_access_logs"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(
        UUID(as_uuid=True),
        ForeignKey("support_access_requests.id"),
        nullable=False,
    )
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)

    action = Column(
        SAEnum(*SUPPORT_ACCESS_LOG_ACTIONS, name="support_access_log_action_enum"),
        nullable=False,
    )

    actor_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_email = Column(String(255), nullable=True)   # denormalized
    actor_role  = Column(String(50),  nullable=True)   # denormalized

    # For RESOURCE_ACCESSED entries — what was read under the grant.
    resource_type = Column(String(100), nullable=True)  # one of SUPPORT_ACCESS_SCOPES
    resource_id   = Column(String(100), nullable=True)
    endpoint_path = Column(String(500), nullable=True)
    http_method   = Column(String(10),  nullable=True)

    detail     = Column(Text, nullable=True)   # JSON string of additional context
    ip_address = Column(String(45), nullable=True)
    timestamp  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_support_access_logs_request_id", "request_id"),
        Index("ix_support_access_logs_org_id", "organization_id"),
        Index("ix_support_access_logs_action", "action"),
        Index("ix_support_access_logs_timestamp", "timestamp"),
    )