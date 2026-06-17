"""
app/services/support_access.py
================================
Phase 6 — Support Access Workflow Service.

Responsibilities
-----------------
1. Validate requested/granted scopes against SUPPORT_ACCESS_SCOPES.
2. Mint short-lived, scope-limited "support access grant" JWTs on approval.
3. Decode and validate support access grant tokens, enforcing:
     - not expired
     - not revoked
     - scope matches the resource being accessed
     - org matches the resource's organization
4. Write append-only support_access_logs entries for every lifecycle
   transition and every resource access made under a grant.
5. Sweep expired-but-still-"approved" requests to "expired".

Public API
----------
    validate_scopes(scopes)
        → raises HTTPException(400) on unknown scope names

    create_access_grant_token(request, granted_to_user)
        → (token: str, jti: str) — short-lived JWT scoped to the request

    decode_access_grant_token(token)
        → dict payload, raises HTTPException(401) if invalid/expired

    enforce_support_access(db, current_user, org_id, scope, request)
        → SupportAccessRequest if the caller holds a valid, active grant
          covering (org_id, scope); raises HTTPException(403) otherwise.
          Also writes a RESOURCE_ACCESSED log row.

    log_support_access_event(db, request_row, action, actor, ...)
        → SupportAccessLog

    expire_stale_requests(db)
        → marks any "approved" requests past expires_at as "expired"
          and writes ACCESS_EXPIRED log rows. Idempotent.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.models_support import (
    SUPPORT_ACCESS_SCOPES,
    SupportAccessLog,
    SupportAccessRequest,
)
from app.services.auth import ALGORITHM, SECRET_KEY

# ── Config ─────────────────────────────────────────────────────────────────────
# Hard ceiling on how long a single support access grant may last, regardless
# of what was requested. Customers and platform admins can shorten this, but
# never extend beyond it.
MAX_GRANT_MINUTES = 480  # 8 hours


# ── Scope validation ──────────────────────────────────────────────────────────

def validate_scopes(scopes: list[str]) -> None:
    """Raise HTTP 400 if any scope is not a recognised support access scope."""
    invalid = [s for s in scopes if s not in SUPPORT_ACCESS_SCOPES]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown support access scopes: {invalid}. "
                   f"Valid scopes: {list(SUPPORT_ACCESS_SCOPES)}",
        )


def clamp_minutes(requested_minutes: int) -> int:
    """Clamp a requested grant duration to (0, MAX_GRANT_MINUTES]."""
    if requested_minutes <= 0:
        return 60
    return min(requested_minutes, MAX_GRANT_MINUTES)


# ── Grant token issuance ─────────────────────────────────────────────────────

def create_access_grant_token(
    req: SupportAccessRequest,
    granted_to: User,
) -> tuple[str, str]:
    """
    Mint a short-lived JWT representing an approved support access grant.

    The token carries:
      - sub:      the support engineer's user id (granted_to)
      - jti:      unique grant id, also stored on the request row
      - org_id:   the organization the grant applies to
      - scopes:   list of granted scopes
      - grant_id: the SupportAccessRequest.id
      - exp:      expiry (mirrors req.expires_at)
      - type:     "support_access_grant" — distinguishes from normal session tokens

    Returns (token, jti).
    """
    jti = uuid.uuid4().hex
    expires_at = req.expires_at
    if expires_at is None:
        raise ValueError("expires_at must be set before minting a grant token")

    payload = {
        "sub":      str(granted_to.id),
        "role":     granted_to.role,
        "jti":      jti,
        "org_id":   str(req.organization_id),
        "scopes":   req.granted_scopes or [],
        "grant_id": str(req.id),
        "type":     "support_access_grant",
        "exp":      expires_at,
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)
    return token, jti


def decode_access_grant_token(token: str) -> dict:
    """Decode and validate a support access grant JWT. Raises HTTP 401 on failure."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired support access grant token",
        )

    if payload.get("type") != "support_access_grant":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not a support access grant",
        )

    return payload


# ── Enforcement (used by other endpoints to allow scoped support access) ───────

def enforce_support_access(
    db: Session,
    grant_payload: dict,
    org_id: str,
    scope: str,
    current_user: User,
    request: Optional[Request] = None,
    resource_id: Optional[str] = None,
) -> SupportAccessRequest:
    """
    Validate that a decoded support access grant payload authorizes the
    caller to access `scope` within `org_id`. Writes a RESOURCE_ACCESSED
    log row on success.

    Raises HTTP 403 if:
      - org_id does not match the grant
      - scope is not in the grant's granted_scopes
      - the underlying request is no longer "approved" (revoked/expired)
    """
    if str(grant_payload.get("org_id")) != str(org_id):
        raise HTTPException(403, "Support access grant does not cover this organization")

    granted_scopes = grant_payload.get("scopes") or []
    if scope not in granted_scopes:
        raise HTTPException(403, f"Support access grant does not cover scope '{scope}'")

    grant_id = grant_payload.get("grant_id")
    req = db.query(SupportAccessRequest).filter(SupportAccessRequest.id == grant_id).first()
    if not req:
        raise HTTPException(403, "Support access grant not found")

    if req.status != "approved":
        raise HTTPException(403, f"Support access grant is {req.status}, not approved")

    now = datetime.now(timezone.utc)
    expires_at = req.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and expires_at < now:
        req.status = "expired"
        db.flush()
        log_support_access_event(
            db, req, "ACCESS_EXPIRED", current_user,
            detail={"reason": "grant expired at time of access"},
            request=request,
        )
        raise HTTPException(403, "Support access grant has expired")

    log_support_access_event(
        db, req, "RESOURCE_ACCESSED", current_user,
        resource_type=scope,
        resource_id=resource_id,
        request=request,
    )
    return req


# ── Logging ───────────────────────────────────────────────────────────────────

def log_support_access_event(
    db: Session,
    req: SupportAccessRequest,
    action: str,
    actor: Optional[User],
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    detail: Optional[dict] = None,
    request: Optional[Request] = None,
) -> SupportAccessLog:
    """Write an immutable support_access_logs entry."""
    ip_address = None
    if request:
        forwarded_for = request.headers.get("X-Forwarded-For")
        ip_address = (
            forwarded_for.split(",")[0].strip()
            if forwarded_for
            else request.client.host if request.client else None
        )

    entry = SupportAccessLog(
        request_id      = req.id,
        organization_id = req.organization_id,
        action          = action,
        actor_id        = actor.id    if actor else None,
        actor_email     = actor.email if actor else None,
        actor_role      = actor.role  if actor else "system",
        resource_type   = resource_type,
        resource_id     = resource_id,
        endpoint_path   = str(request.url.path) if request else None,
        http_method     = request.method        if request else None,
        detail          = json.dumps(detail, default=str) if detail else None,
        ip_address      = ip_address,
    )
    db.add(entry)
    db.flush()
    return entry


# ── Maintenance ──────────────────────────────────────────────────────────────

def expire_stale_requests(db: Session) -> int:
    """
    Mark any "approved" support access requests whose expires_at has passed
    as "expired", writing ACCESS_EXPIRED log rows. Returns the count expired.

    Idempotent — safe to call on a schedule or lazily before list/history reads.
    """
    now = datetime.now(timezone.utc)
    stale = (
        db.query(SupportAccessRequest)
        .filter(
            SupportAccessRequest.status == "approved",
            SupportAccessRequest.expires_at.isnot(None),
            SupportAccessRequest.expires_at < now,
        )
        .all()
    )
    for req in stale:
        req.status = "expired"
        log_support_access_event(
            db, req, "ACCESS_EXPIRED", None,
            detail={"reason": "grant window elapsed"},
        )
    if stale:
        db.commit()
    return len(stale)


def compute_expiry(minutes: int) -> datetime:
    """Return a tz-aware UTC datetime `minutes` from now."""
    return datetime.now(timezone.utc) + timedelta(minutes=clamp_minutes(minutes))