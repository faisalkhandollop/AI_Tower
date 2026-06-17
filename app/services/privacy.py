"""
app/services/privacy.py
========================
Phase 5 — Privacy Enforcement Service.

Responsibilities
----------------
1. Enforce SUPER_ADMIN blackout from customer content (conversations, messages,
   attachments, knowledge bases, API keys, usage log detail).
2. Enforce chat visibility rules per org's privacy_settings.chat_visibility.
3. Gate data exports against org privacy settings.
4. Write SuperAdminAccessBlock rows when enforcement fires.
5. Bootstrap default PrivacySettings for new organizations.

Public API
----------
    enforce_super_admin_blackout(db, current_user, resource, org_id, request)
        → raises HTTP 403 and logs a block if caller is SUPER_ADMIN

    assert_chat_visible(db, current_user, conversation_org_id, target_user_id)
        → raises HTTP 403 if caller cannot view that conversation under org policy

    check_export_allowed(db, current_user, org_id, scope, reason, request)
        → returns ExportDecision (allowed/blocked + reason)

    get_or_create_privacy_settings(db, org_id) → PrivacySettings

Usage from endpoints
--------------------
    from app.services.privacy import enforce_super_admin_blackout

    @router.get("/conversations/org/{org_id}/all")
    def admin_list_conversations(org_id, db=Depends(get_db),
                                  current_user=Depends(get_current_user),
                                  request: Request = None):
        enforce_super_admin_blackout(db, current_user, "conversations", org_id, request)
        ...
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.models_privacy import (
    ACCESS_DECISION,           # noqa: F401 — re-exported for convenience
    DATA_CATEGORIES,
    ExportControlLog,
    PrivacySettings,
    SuperAdminAccessBlock,
)

# ── Constants ─────────────────────────────────────────────────────────────────

# Data categories that SUPER_ADMIN is *always* blocked from regardless of settings.
_SUPER_ADMIN_BLOCKED_RESOURCES = frozenset({
    "conversations",
    "messages",
    "attachments",
    "knowledge_base",
    "api_keys",
    "usage_logs_detail",   # the full prompt/response columns — aggregate stats are OK
    "user_pii",            # individual user email/name records within an org
})

# Map chat_visibility setting → minimum role rank required to view conversations
# Lower number = higher privilege.
_VISIBILITY_RANK = {
    "none":                 0,   # nobody except the user themselves
    "org_admin_only":       1,
    "manager_and_above":    2,
    "team_lead_and_above":  3,
}

_ROLE_RANK = {
    "SUPER_ADMIN":          -1,  # explicitly blocked for chat content
    "ORG_ADMIN":             1,
    "ADMIN":                 1,  # legacy alias
    "DEPARTMENT_MANAGER":    2,
    "MANAGER":               2,  # legacy alias
    "TEAM_LEAD":             3,
    "USER":                  4,
    "EMPLOYEE":              4,  # legacy alias
}


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _role_upper(user: User) -> str:
    return (user.role or "USER").upper()


def _is_super_admin(user: User) -> bool:
    return _role_upper(user) == "SUPER_ADMIN"


def _get_ip(request: Optional[Request]) -> Optional[str]:
    if not request:
        return None
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ─────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────

def get_or_create_privacy_settings(
    db: Session,
    org_id: str,
) -> PrivacySettings:
    """
    Return the PrivacySettings row for this org, creating it with safe defaults
    if it doesn't exist yet.

    Called automatically on org creation and by enforcement functions.
    """
    try:
        oid = uuid.UUID(org_id)
    except (ValueError, AttributeError):
        raise HTTPException(400, f"Invalid org_id: {org_id}")

    settings = (
        db.query(PrivacySettings)
        .filter(PrivacySettings.organization_id == oid)
        .first()
    )
    if settings:
        return settings

    settings = PrivacySettings(
        organization_id=oid,
        # All super_admin blocks default to True at the model level.
        # Allowed export scopes: non-sensitive by default.
        allowed_export_scopes=["usage_logs", "audit_logs", "user_list"],
    )
    db.add(settings)
    db.flush()
    return settings


# ─────────────────────────────────────────────────────────────
# 1. Super Admin Blackout
# ─────────────────────────────────────────────────────────────

def enforce_super_admin_blackout(
    db:            Session,
    current_user:  User,
    resource:      str,
    org_id:        Optional[str] = None,
    request:       Optional[Request] = None,
) -> None:
    """
    Raise HTTP 403 and write a SuperAdminAccessBlock record if:
      - current_user is SUPER_ADMIN, AND
      - resource is in the blocked set.

    Call this at the top of any endpoint that touches customer content.
    No-op for non-SUPER_ADMIN users.

    Parameters
    ----------
    resource : one of DATA_CATEGORIES (e.g. "conversations", "messages")
    org_id   : UUID string of the org being accessed (for audit trail)
    """
    if not _is_super_admin(current_user):
        return

    if resource not in _SUPER_ADMIN_BLOCKED_RESOURCES:
        return  # this resource is visible to super admin (e.g. platform metrics)

    # Resolve org name for the block log
    org_name: Optional[str] = None
    if org_id:
        try:
            from app.db.models import Organization
            org = db.query(Organization).filter(
                Organization.id == uuid.UUID(org_id)
            ).first()
            if org:
                org_name = org.name
        except Exception:
            pass

    endpoint_path  = request.url.path  if request else None
    http_method    = request.method    if request else None

    block = SuperAdminAccessBlock(
        super_admin_id    = current_user.id,
        super_admin_email = current_user.email,
        organization_id   = uuid.UUID(org_id) if org_id else None,
        organization_name = org_name,
        attempted_resource= resource,
        endpoint_path     = endpoint_path,
        http_method       = http_method,
        block_reason      = (
            f"SUPER_ADMIN is prohibited from accessing customer '{resource}' "
            "per platform privacy policy (Phase 5)"
        ),
        ip_address        = _get_ip(request),
    )
    db.add(block)
    db.flush()
    # Commit so the block is persisted even though we raise immediately after.
    db.commit()

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Access denied: Super Admin cannot access customer {resource}. "
            "This attempt has been logged."
        ),
    )


def block_if_super_admin(resource: str):
    """
    FastAPI dependency factory.  Drop-in for simple endpoints.

    Usage:
        @router.get("/conversations/{conv_id}")
        def get_conv(
            conv_id: str,
            _: None = Depends(block_if_super_admin("conversations")),
            ...
        ):
    """
    from fastapi import Depends
    from app.db.database import get_db
    from app.services.auth import get_current_user

    def dependency(
        request:      Request,
        db:           Session  = Depends(get_db),
        current_user: User     = Depends(get_current_user),
    ) -> None:
        enforce_super_admin_blackout(db, current_user, resource, request=request)

    return dependency


# ─────────────────────────────────────────────────────────────
# 2. Chat Visibility Enforcement
# ─────────────────────────────────────────────────────────────

def assert_chat_visible(
    db:                   Session,
    current_user:         User,
    conversation_org_id:  str,
    conversation_owner_id: str,
) -> None:
    """
    Enforce org-level chat visibility rules.

    A user can ALWAYS view their own conversations.
    Higher-privilege users can view others' conversations only if the
    org's chat_visibility setting grants their role access.

    Raises HTTP 403 if access is not permitted.
    Raises HTTP 403 always for SUPER_ADMIN (blackout takes precedence).
    """
    # Super admin is always blocked from chat content.
    if _is_super_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super Admin cannot view customer conversations.",
        )

    # Users can always see their own.
    try:
        if str(current_user.id) == str(conversation_owner_id):
            return
    except Exception:
        pass

    settings = get_or_create_privacy_settings(db, conversation_org_id)
    visibility = settings.chat_visibility  # e.g. "org_admin_only"

    if visibility == "none":
        raise HTTPException(
            403,
            "Organization policy: conversations are private (no cross-user visibility).",
        )

    required_rank = _VISIBILITY_RANK.get(visibility, 1)
    caller_role   = _role_upper(current_user)
    caller_rank   = _ROLE_RANK.get(caller_role, 4)  # default to USER rank

    if caller_rank > required_rank:
        raise HTTPException(
            403,
            f"Organization policy: your role ({caller_role}) cannot view other users' "
            f"conversations under the current visibility setting ({visibility}).",
        )


# ─────────────────────────────────────────────────────────────
# 3. Export Controls
# ─────────────────────────────────────────────────────────────

@dataclass
class ExportDecision:
    allowed:      bool
    block_reason: Optional[str] = None
    record_count: Optional[int] = None


def check_export_allowed(
    db:           Session,
    current_user: User,
    org_id:       str,
    scope:        str,         # one of EXPORT_CONTROL_SCOPES
    reason:       Optional[str] = None,
    export_format: str = "json",
    filters:      Optional[dict] = None,
    request:      Optional[Request] = None,
) -> ExportDecision:
    """
    Gate a data export against org privacy settings.

    Records an ExportControlLog row regardless of outcome.

    Returns ExportDecision.  Does NOT raise — caller decides how to respond.
    """
    # Super admin cannot export org content either.
    if _is_super_admin(current_user):
        _record_export(
            db, current_user, org_id, scope, export_format, filters, reason,
            decision="blocked",
            block_reason="SUPER_ADMIN cannot export customer data",
            request=request,
        )
        return ExportDecision(
            allowed=False,
            block_reason="Super Admin is prohibited from exporting customer data.",
        )

    settings = get_or_create_privacy_settings(db, org_id)

    if not settings.exports_enabled:
        _record_export(
            db, current_user, org_id, scope, export_format, filters, reason,
            decision="blocked",
            block_reason="exports_disabled",
            request=request,
        )
        return ExportDecision(
            allowed=False,
            block_reason="Data exports are disabled for this organization.",
        )

    allowed_scopes = settings.allowed_export_scopes or []
    if scope not in allowed_scopes:
        _record_export(
            db, current_user, org_id, scope, export_format, filters, reason,
            decision="blocked",
            block_reason=f"scope '{scope}' not in allowed_export_scopes",
            request=request,
        )
        return ExportDecision(
            allowed=False,
            block_reason=f"Exporting '{scope}' is not permitted by this organization's policy.",
        )

    if settings.export_requires_reason and not (reason or "").strip():
        _record_export(
            db, current_user, org_id, scope, export_format, filters, reason,
            decision="blocked",
            block_reason="reason_required_but_missing",
            request=request,
        )
        return ExportDecision(
            allowed=False,
            block_reason="A written reason is required before exporting data.",
        )

    _record_export(
        db, current_user, org_id, scope, export_format, filters, reason,
        decision="allowed",
        request=request,
    )
    return ExportDecision(allowed=True)


def _record_export(
    db:            Session,
    current_user:  User,
    org_id:        str,
    scope:         str,
    export_format: str,
    filters:       Optional[dict],
    reason:        Optional[str],
    decision:      str,
    block_reason:  Optional[str] = None,
    record_count:  Optional[int] = None,
    request:       Optional[Request] = None,
) -> None:
    """Internal helper — write ExportControlLog row."""
    try:
        oid = uuid.UUID(org_id)
    except (ValueError, AttributeError):
        oid = None

    entry = ExportControlLog(
        organization_id = oid,
        requested_by    = current_user.id,
        requester_role  = _role_upper(current_user),
        requester_email = current_user.email,
        scope           = scope,
        export_format   = export_format,
        filters_applied = filters,
        reason          = reason,
        decision        = decision,
        block_reason    = block_reason,
        record_count    = record_count,
        ip_address      = _get_ip(request),
    )
    db.add(entry)
    db.flush()


# ─────────────────────────────────────────────────────────────
# 4. Validation helpers (used by settings API)
# ─────────────────────────────────────────────────────────────

_CUSTOMER_DATA_SCOPES = frozenset({
    "conversations",
    "messages",
    "attachments",
    "knowledge_base",
    "api_keys",
})


def validate_policy_roles(roles: list[str]) -> list[str]:
    """
    Reject any attempt to include SUPER_ADMIN in a data-access policy
    that covers customer data categories.

    Returns sanitised list; raises ValueError if SUPER_ADMIN is present.
    """
    bad = [r for r in roles if r.upper() == "SUPER_ADMIN"]
    if bad:
        raise ValueError(
            "SUPER_ADMIN cannot be granted access to customer data categories "
            "through a data access policy."
        )
    return roles


def validate_export_scopes(scopes: list[str]) -> list[str]:
    """
    Ensure no scope grants super_admin-equivalent access.
    Super admin export restriction is enforced separately;
    this validates org admin configuration.
    """
    from app.db.models_privacy import EXPORT_CONTROL_SCOPES
    invalid = [s for s in scopes if s not in EXPORT_CONTROL_SCOPES]
    if invalid:
        raise ValueError(f"Unknown export scope(s): {invalid}")
    return scopes