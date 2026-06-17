"""
app/db/models_security.py
==========================
Phase 7 — Security Operations Center Models.

New tables:
  login_history        — immutable record of every authentication attempt
                         (success or failure) per user and IP address
  user_mfa             — per-user MFA configuration and state
                         (TOTP, backup codes, status)
  api_tokens           — long-lived API tokens (alternative to short-lived JWT;
                         used by integrations and service accounts)
  password_resets      — one-time password reset tokens with expiry + usage tracking
  email_verifications  — one-time email verification tokens (initial registration
                         and email-change flows)

Architecture notes:
  - login_history is append-only; never update rows, only insert.
  - user_mfa has one row per user; upsert semantics on creation.
  - api_tokens carry hashed values (SHA-256) — raw tokens are returned
    only at creation time and never stored in plaintext.
  - password_resets and email_verifications share the same one-time-use,
    time-limited pattern: is_used + expires_at guard every redemption.
  - All tables are owned by Phase 7; no existing tables are modified.
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
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# ENUMS
# ─────────────────────────────────────────────────────────────

LOGIN_EVENT_TYPES = (
    "success",          # credentials matched, token issued
    "failed_password",  # credentials presented but password wrong
    "failed_mfa",       # password ok but MFA challenge failed
    "account_locked",   # too many consecutive failures
    "account_inactive", # account deactivated
    "invalid_token",    # Bearer token presented was expired / tampered
)

MFA_METHODS = (
    "totp",         # TOTP via authenticator app (RFC 6238)
    "backup_code",  # single-use backup code
    "email_otp",    # OTP sent to registered email
    "sms_otp",      # OTP sent to registered phone number
)

MFA_STATUSES = (
    "disabled",     # MFA not configured or explicitly turned off
    "pending",      # setup initiated; verification not yet confirmed
    "active",       # fully enrolled and required on login
)

TOKEN_STATUSES = (
    "active",   # token is live and can authenticate
    "revoked",  # manually revoked by user or admin
    "expired",  # past its expires_at; treated as revoked
)


# ─────────────────────────────────────────────────────────────
# LOGIN HISTORY
# Append-only. One row per authentication attempt (success or
# failure), including API-token-based authentications.
# ─────────────────────────────────────────────────────────────
class LoginHistory(Base):
    __tablename__ = "login_history"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # nullable because failed attempts may reference a non-existent email

    # ── Attempt context ────────────────────────────────────────
    email_attempted = Column(String(255), nullable=True)    # what was typed in the login field
    event_type      = Column(
        SAEnum(*LOGIN_EVENT_TYPES, name="login_event_type_enum"),
        nullable=False,
    )

    # ── Network context ────────────────────────────────────────
    ip_address  = Column(String(45),  nullable=True)   # IPv4 or IPv6 (up to 45 chars)
    user_agent  = Column(Text,        nullable=True)
    geo_country = Column(String(2),   nullable=True)   # ISO 3166-1 alpha-2
    geo_city    = Column(String(100), nullable=True)

    # ── Auth method ────────────────────────────────────────────
    auth_method = Column(String(50), nullable=True)    # "password" | "api_token" | "sso"
    mfa_used    = Column(Boolean, nullable=False, default=False)
    mfa_method  = Column(String(20), nullable=True)    # value from MFA_METHODS or null

    # ── Failure detail ────────────────────────────────────────
    failure_reason  = Column(Text, nullable=True)       # machine-readable detail
    consecutive_failures = Column(Integer, nullable=False, default=0)
    # snapshot at time of event; useful for lockout investigations

    # ── Session binding ───────────────────────────────────────
    session_token_jti = Column(String(64), nullable=True)  # JWT jti if a token was issued

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_login_history_user_id",    "user_id"),
        Index("ix_login_history_event_type", "event_type"),
        Index("ix_login_history_ip_address", "ip_address"),
        Index("ix_login_history_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────
# USER MFA
# One row per user. Upsert on enrollment; never deleted —
# status transitions track the lifecycle.
# ─────────────────────────────────────────────────────────────
class UserMFA(Base):
    __tablename__ = "user_mfa"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, unique=True)

    # ── Enrollment state ───────────────────────────────────────
    status = Column(
        SAEnum(*MFA_STATUSES, name="mfa_status_enum"),
        nullable=False,
        default="disabled",
    )
    method = Column(
        SAEnum(*MFA_METHODS, name="mfa_method_enum"),
        nullable=True,
    )

    # ── TOTP fields (method="totp") ────────────────────────────
    totp_secret_enc   = Column(Text, nullable=True)    # AES-256-GCM encrypted Base32 seed
    totp_confirmed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Backup codes (hashed; stored as JSON array of SHA-256 hex strings) ────
    backup_codes_hashed = Column(JSON, nullable=False, default=list)
    backup_codes_used   = Column(Integer, nullable=False, default=0)
    # count of consumed backup codes; codes themselves are removed from the
    # array on use, so length + used = original count

    # ── Phone / email OTP fields ────────────────────────────────
    otp_phone_enc = Column(String(255), nullable=True)  # encrypted E.164 phone
    otp_email     = Column(String(255), nullable=True)  # OTP delivery email (may differ from login email)

    # ── Grace & enforcement ────────────────────────────────────
    grace_period_ends_at   = Column(DateTime(timezone=True), nullable=True)
    # admins can grant a temporary grace window before enforcement kicks in
    enforcement_required   = Column(Boolean, nullable=False, default=False)
    # set True by policy when the org mandates MFA for this role/dept

    # ── Audit timestamps ───────────────────────────────────────
    enrolled_at   = Column(DateTime(timezone=True), nullable=True)
    last_used_at  = Column(DateTime(timezone=True), nullable=True)
    disabled_at   = Column(DateTime(timezone=True), nullable=True)
    disabled_by   = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_user_mfa_user_id", "user_id"),
        Index("ix_user_mfa_status",  "status"),
    )


# ─────────────────────────────────────────────────────────────
# API TOKENS
# Long-lived tokens for programmatic / integration access.
# Raw token = prefix + "." + random_hex (64 chars total).
# Only token_hash (SHA-256) is persisted; the raw token is
# returned once at creation and must be saved by the caller.
# ─────────────────────────────────────────────────────────────
class APIToken(Base):
    __tablename__ = "api_tokens"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # ── Identity ───────────────────────────────────────────────
    name        = Column(String(200), nullable=False)    # human label e.g. "CI/CD Pipeline"
    description = Column(Text, nullable=True)
    token_prefix = Column(String(12), nullable=False)    # first 8 chars of raw token for display
    token_hash   = Column(String(64), nullable=False, unique=True)  # SHA-256 hex

    # ── Scope & permissions ────────────────────────────────────
    scopes = Column(JSON, nullable=False, default=list)
    # list of permission strings e.g. ["usage:read", "reports:read"]

    # ── Lifecycle ──────────────────────────────────────────────
    status = Column(
        SAEnum(*TOKEN_STATUSES, name="api_token_status_enum"),
        nullable=False,
        default="active",
    )
    expires_at  = Column(DateTime(timezone=True), nullable=True)   # null = never

    # ── Usage tracking ─────────────────────────────────────────
    last_used_at   = Column(DateTime(timezone=True), nullable=True)
    last_used_ip   = Column(String(45), nullable=True)
    request_count  = Column(Integer, nullable=False, default=0)

    # ── Revocation ────────────────────────────────────────────
    revoked_at     = Column(DateTime(timezone=True), nullable=True)
    revoked_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    revoke_reason  = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_api_tokens_user_id",    "user_id"),
        Index("ix_api_tokens_token_hash", "token_hash"),
        Index("ix_api_tokens_status",     "status"),
    )


# ─────────────────────────────────────────────────────────────
# PASSWORD RESETS
# One-time-use, time-limited tokens for the "forgot password"
# flow. Tokens are stored hashed; raw values go to the user's
# inbox only.
# ─────────────────────────────────────────────────────────────
class PasswordReset(Base):
    __tablename__ = "password_resets"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # ── Token ─────────────────────────────────────────────────
    token_hash   = Column(String(64), nullable=False, unique=True)  # SHA-256 hex of raw token
    expires_at   = Column(DateTime(timezone=True), nullable=False)

    # ── Usage ─────────────────────────────────────────────────
    is_used      = Column(Boolean, nullable=False, default=False)
    used_at      = Column(DateTime(timezone=True), nullable=True)
    used_ip      = Column(String(45), nullable=True)

    # ── Request context ────────────────────────────────────────
    requested_ip    = Column(String(45), nullable=True)
    requested_email = Column(String(255), nullable=True)  # denormalized for audit

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_password_resets_user_id",    "user_id"),
        Index("ix_password_resets_token_hash", "token_hash"),
        Index("ix_password_resets_is_used",    "is_used"),
    )


# ─────────────────────────────────────────────────────────────
# EMAIL VERIFICATIONS
# Used for initial account activation and email-change flows.
# Same one-time-use / time-limited pattern as password resets.
# ─────────────────────────────────────────────────────────────
class EmailVerification(Base):
    __tablename__ = "email_verifications"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # ── Token ─────────────────────────────────────────────────
    token_hash = Column(String(64), nullable=False, unique=True)  # SHA-256 hex
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # ── Purpose ────────────────────────────────────────────────
    verification_type = Column(
        String(30),
        nullable=False,
        default="registration",
    )
    # "registration" | "email_change"

    # ── Addresses ─────────────────────────────────────────────
    email_to_verify   = Column(String(255), nullable=False)   # target address being verified
    previous_email    = Column(String(255), nullable=True)    # only set for "email_change" type

    # ── Usage ─────────────────────────────────────────────────
    is_used    = Column(Boolean, nullable=False, default=False)
    used_at    = Column(DateTime(timezone=True), nullable=True)
    used_ip    = Column(String(45), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_email_verifications_user_id",    "user_id"),
        Index("ix_email_verifications_token_hash", "token_hash"),
        Index("ix_email_verifications_is_used",    "is_used"),
    )