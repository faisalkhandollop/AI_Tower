"""
app/api/security.py
=====================
Phase 7 — Security Operations Center API.

Routes
------
GET /security/logins    → paginated login history with failure filtering
GET /security/mfa       → MFA adoption stats + per-user status
GET /security/tokens    → active / revoked API token inventory
GET /security/sessions  → currently active sessions with risk signals

All endpoints require admin role.
Super-admin gets platform-wide view; admin gets org-scoped view (future
RBAC extension — currently both return platform-wide data consistent with
the existing admin pattern in this codebase).

Suspicious Activity signals (returned inline on relevant endpoints):
  - ≥5 failed login attempts from the same IP within 15 minutes
  - Login from a new country for a user (compared against their history)
  - API token with request_count spike (>1000 calls in 24 h)
  - Session still active with last_active > 8 h ago
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session as DBSession

from app.db.database import get_db
from app.db.models import Session as ChatSession, User
from app.db.models_security import (
    APIToken,
    EmailVerification,
    LoginHistory,
    PasswordReset,
    UserMFA,
)
from app.services.auth import get_current_user, require_admin

router = APIRouter(prefix="/security", tags=["Security Operations Center"])


# ── constants ─────────────────────────────────────────────────────────────────

_BRUTE_FORCE_WINDOW_MINUTES = 15
_BRUTE_FORCE_FAILURE_THRESHOLD = 5
_SESSION_STALE_HOURS = 8
_TOKEN_SPIKE_REQUESTS = 1000   # within 24 h — flag for manual review


# ── helpers ───────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _flag_suspicious_ips(db: DBSession) -> set[str]:
    """
    Return the set of IP addresses that have ≥ THRESHOLD failed login
    attempts within the brute-force window.
    """
    cutoff = _utcnow() - timedelta(minutes=_BRUTE_FORCE_WINDOW_MINUTES)
    rows = (
        db.query(LoginHistory.ip_address, func.count(LoginHistory.id).label("cnt"))
        .filter(
            LoginHistory.created_at >= cutoff,
            LoginHistory.event_type.in_(
                ["failed_password", "failed_mfa", "account_locked"]
            ),
            LoginHistory.ip_address.isnot(None),
        )
        .group_by(LoginHistory.ip_address)
        .having(func.count(LoginHistory.id) >= _BRUTE_FORCE_FAILURE_THRESHOLD)
        .all()
    )
    return {r.ip_address for r in rows}


def _new_country_users(db: DBSession) -> set[str]:
    """
    Return user_id strings for users whose most recent successful login
    came from a country not seen in their prior successful logins.
    Simplified heuristic: compare last login's geo_country against the
    set of distinct countries in their entire history (excluding last row).
    """
    suspicious: set[str] = set()

    # users who have at least 2 successful logins
    subq = (
        db.query(LoginHistory.user_id)
        .filter(
            LoginHistory.event_type == "success",
            LoginHistory.user_id.isnot(None),
            LoginHistory.geo_country.isnot(None),
        )
        .group_by(LoginHistory.user_id)
        .having(func.count(LoginHistory.id) >= 2)
        .subquery()
    )

    candidates = db.query(subq.c.user_id).all()

    for (uid,) in candidates:
        records = (
            db.query(LoginHistory.geo_country, LoginHistory.created_at)
            .filter(
                LoginHistory.user_id == uid,
                LoginHistory.event_type == "success",
                LoginHistory.geo_country.isnot(None),
            )
            .order_by(LoginHistory.created_at.desc())
            .limit(20)
            .all()
        )
        if len(records) < 2:
            continue
        last_country = records[0].geo_country
        historical = {r.geo_country for r in records[1:]}
        if last_country not in historical:
            suspicious.add(str(uid))

    return suspicious


# ── serialisers ───────────────────────────────────────────────────────────────

def _login_dict(e: LoginHistory, suspicious_ips: set[str]) -> dict:
    return {
        "id":                   str(e.id),
        "user_id":              str(e.user_id) if e.user_id else None,
        "email_attempted":      e.email_attempted,
        "event_type":           e.event_type,
        "ip_address":           e.ip_address,
        "user_agent":           e.user_agent,
        "geo_country":          e.geo_country,
        "geo_city":             e.geo_city,
        "auth_method":          e.auth_method,
        "mfa_used":             e.mfa_used,
        "mfa_method":           e.mfa_method,
        "failure_reason":       e.failure_reason,
        "consecutive_failures": e.consecutive_failures,
        "session_token_jti":    e.session_token_jti,
        "created_at":           str(e.created_at),
        "is_suspicious_ip":     (e.ip_address in suspicious_ips) if e.ip_address else False,
    }


def _mfa_dict(mfa: UserMFA) -> dict:
    return {
        "id":                   str(mfa.id),
        "user_id":              str(mfa.user_id),
        "status":               mfa.status,
        "method":               mfa.method,
        "enforcement_required": mfa.enforcement_required,
        "backup_codes_remaining": (
            len(mfa.backup_codes_hashed) if mfa.backup_codes_hashed else 0
        ),
        "enrolled_at":          str(mfa.enrolled_at)  if mfa.enrolled_at  else None,
        "last_used_at":         str(mfa.last_used_at) if mfa.last_used_at else None,
        "grace_period_ends_at": str(mfa.grace_period_ends_at) if mfa.grace_period_ends_at else None,
        "updated_at":           str(mfa.updated_at),
    }


def _token_dict(t: APIToken) -> dict:
    is_expired = bool(t.expires_at and t.expires_at < _utcnow().replace(tzinfo=None))
    return {
        "id":            str(t.id),
        "user_id":       str(t.user_id),
        "name":          t.name,
        "description":   t.description,
        "token_prefix":  t.token_prefix,
        "scopes":        t.scopes or [],
        "status":        "expired" if (t.status == "active" and is_expired) else t.status,
        "expires_at":    str(t.expires_at) if t.expires_at else None,
        "last_used_at":  str(t.last_used_at) if t.last_used_at else None,
        "last_used_ip":  t.last_used_ip,
        "request_count": t.request_count,
        "revoked_at":    str(t.revoked_at) if t.revoked_at else None,
        "revoked_by":    str(t.revoked_by) if t.revoked_by else None,
        "revoke_reason": t.revoke_reason,
        "created_at":    str(t.created_at),
        "is_high_usage": t.request_count >= _TOKEN_SPIKE_REQUESTS,
    }


def _session_dict(s: ChatSession, stale_threshold: datetime) -> dict:
    last_active_naive = s.last_active.replace(tzinfo=None) if s.last_active else None
    threshold_naive   = stale_threshold.replace(tzinfo=None)
    is_stale = bool(last_active_naive and last_active_naive < threshold_naive)
    return {
        "session_id":          s.session_id,
        "user_id":             str(s.user_id) if s.user_id else None,
        "message_count":       s.message_count,
        "total_tokens_used":   s.total_tokens_used,
        "avg_complexity":      s.avg_complexity,
        "token_budget_status": s.token_budget_status,
        "topics_discussed":    s.topics_discussed or [],
        "files_uploaded":      s.files_uploaded   or [],
        "created_at":          str(s.created_at),
        "last_active":         str(s.last_active),
        "is_stale":            is_stale,
    }


# ── GET /security/logins ──────────────────────────────────────────────────────

@router.get(
    "/logins",
    summary="Login history — all authentication attempts with failure and risk signals",
)
def get_login_history(
    user_id:      Optional[str] = Query(None,  description="Filter by user UUID"),
    ip_address:   Optional[str] = Query(None,  description="Filter by exact IP"),
    event_type:   Optional[str] = Query(None,  description="Filter by event type (e.g. 'failed_password')"),
    failures_only: bool         = Query(False, description="Return only non-success events"),
    suspicious_only: bool       = Query(False, description="Return only events from flagged IPs"),
    days:         int           = Query(7,     ge=1, le=90, description="Look-back window in days"),
    limit:        int           = Query(100,   ge=1, le=500),
    offset:       int           = Query(0,     ge=0),
    db:     DBSession           = Depends(get_db),
    _admin: User                = Depends(require_admin),
):
    """
    Returns paginated login events with suspicious-IP flags.

    **Failed Logins** — use `failures_only=true` or `event_type=failed_password`
    to isolate failure events and feed the dashboard failed-login counter.

    **Suspicious Activity** — IPs with ≥5 failures in 15 min are flagged;
    use `suspicious_only=true` to surface only those events.
    """
    cutoff = _utcnow() - timedelta(days=days)

    suspicious_ips = _flag_suspicious_ips(db)
    suspicious_user_ids = _new_country_users(db)

    q = db.query(LoginHistory).filter(LoginHistory.created_at >= cutoff)

    if user_id:
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format")
        q = q.filter(LoginHistory.user_id == uid)

    if ip_address:
        q = q.filter(LoginHistory.ip_address == ip_address)

    if event_type:
        q = q.filter(LoginHistory.event_type == event_type)

    if failures_only:
        q = q.filter(LoginHistory.event_type != "success")

    if suspicious_only:
        if not suspicious_ips:
            return {
                "total": 0,
                "offset": offset,
                "limit": limit,
                "suspicious_ips": [],
                "suspicious_user_ids": list(suspicious_user_ids),
                "events": [],
            }
        q = q.filter(LoginHistory.ip_address.in_(suspicious_ips))

    total = q.count()
    events = (
        q.order_by(LoginHistory.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Summary counters for the dashboard
    window_cutoff = _utcnow() - timedelta(hours=24)
    failed_24h = (
        db.query(func.count(LoginHistory.id))
        .filter(
            LoginHistory.created_at >= window_cutoff,
            LoginHistory.event_type != "success",
        )
        .scalar()
    ) or 0

    return {
        "total":                 total,
        "offset":                offset,
        "limit":                 limit,
        "failed_24h":            failed_24h,
        "suspicious_ip_count":   len(suspicious_ips),
        "suspicious_ips":        list(suspicious_ips),
        "suspicious_user_ids":   list(suspicious_user_ids),
        "events":                [_login_dict(e, suspicious_ips) for e in events],
    }


# ── GET /security/mfa ─────────────────────────────────────────────────────────

@router.get(
    "/mfa",
    summary="MFA adoption — per-user MFA status and platform-wide adoption metrics",
)
def get_mfa_status(
    status:  Optional[str] = Query(None, description="Filter: disabled | pending | active"),
    enforcement_only: bool = Query(False, description="Only users with enforcement_required=true"),
    limit:   int           = Query(100,  ge=1, le=500),
    offset:  int           = Query(0,    ge=0),
    db:      DBSession     = Depends(get_db),
    _admin:  User          = Depends(require_admin),
):
    """
    Returns MFA configuration state for all users.

    **MFA Adoption** dashboard metrics are included in the response top-level:
      - `total_users`         — total user count
      - `mfa_configured`      — users with status=active
      - `mfa_pending`         — users with status=pending
      - `mfa_disabled`        — users with no MFA row or status=disabled
      - `adoption_rate_pct`   — active / total * 100
      - `enforcement_gap`     — users with enforcement_required=true but status != active
    """
    total_users = db.query(func.count(User.id)).scalar() or 0

    active_count  = db.query(func.count(UserMFA.id)).filter(UserMFA.status == "active").scalar()  or 0
    pending_count = db.query(func.count(UserMFA.id)).filter(UserMFA.status == "pending").scalar() or 0
    # users with no MFA row count as "disabled"
    users_with_mfa = db.query(func.count(UserMFA.id)).scalar() or 0
    disabled_count = total_users - active_count - pending_count

    adoption_rate = round((active_count / total_users * 100), 1) if total_users else 0.0

    enforcement_gap = (
        db.query(func.count(UserMFA.id))
        .filter(UserMFA.enforcement_required == True, UserMFA.status != "active")  # noqa: E712
        .scalar()
    ) or 0

    q = db.query(UserMFA)

    if status:
        q = q.filter(UserMFA.status == status)

    if enforcement_only:
        q = q.filter(UserMFA.enforcement_required == True)  # noqa: E712

    total = q.count()
    records = q.order_by(UserMFA.updated_at.desc()).offset(offset).limit(limit).all()

    return {
        # ── Dashboard metrics ──────────────────────────────────
        "total_users":       total_users,
        "mfa_active":        active_count,
        "mfa_pending":       pending_count,
        "mfa_disabled":      disabled_count,
        "adoption_rate_pct": adoption_rate,
        "enforcement_gap":   enforcement_gap,
        # ── Paginated records ──────────────────────────────────
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "records": [_mfa_dict(r) for r in records],
    }


# ── GET /security/tokens ──────────────────────────────────────────────────────

@router.get(
    "/tokens",
    summary="API token inventory — active, revoked, and high-usage token overview",
)
def get_api_tokens(
    user_id:      Optional[str] = Query(None,  description="Filter by owner user UUID"),
    status:       Optional[str] = Query(None,  description="Filter: active | revoked | expired"),
    high_usage_only: bool       = Query(False, description="Only tokens with >1000 total requests"),
    limit:        int           = Query(100,   ge=1, le=500),
    offset:       int           = Query(0,     ge=0),
    db:     DBSession           = Depends(get_db),
    _admin: User                = Depends(require_admin),
):
    """
    Returns the API token inventory for admin oversight.

    **Active Tokens** — status=active and not past expires_at
    **Revoked Tokens** — status=revoked (manually disabled)
    **Expired Tokens** — status=active but expires_at < now (treated as expired)
    **Suspicious / High-Usage** — request_count ≥ 1000; review for abuse
    """
    now = _utcnow()

    # Summary counters
    total_active = (
        db.query(func.count(APIToken.id))
        .filter(
            APIToken.status == "active",
            or_(APIToken.expires_at.is_(None), APIToken.expires_at > now),
        )
        .scalar()
    ) or 0
    total_revoked = (
        db.query(func.count(APIToken.id))
        .filter(APIToken.status == "revoked")
        .scalar()
    ) or 0
    total_expired = (
        db.query(func.count(APIToken.id))
        .filter(APIToken.status == "active", APIToken.expires_at <= now)
        .scalar()
    ) or 0
    total_high_usage = (
        db.query(func.count(APIToken.id))
        .filter(APIToken.request_count >= _TOKEN_SPIKE_REQUESTS)
        .scalar()
    ) or 0

    q = db.query(APIToken)

    if user_id:
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format")
        q = q.filter(APIToken.user_id == uid)

    if status == "expired":
        q = q.filter(APIToken.status == "active", APIToken.expires_at <= now)
    elif status:
        q = q.filter(APIToken.status == status)

    if high_usage_only:
        q = q.filter(APIToken.request_count >= _TOKEN_SPIKE_REQUESTS)

    total = q.count()
    tokens = q.order_by(APIToken.created_at.desc()).offset(offset).limit(limit).all()

    return {
        # ── Dashboard metrics ──────────────────────────────────
        "total_active":     total_active,
        "total_revoked":    total_revoked,
        "total_expired":    total_expired,
        "total_high_usage": total_high_usage,
        # ── Paginated records ──────────────────────────────────
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "tokens": [_token_dict(t) for t in tokens],
    }


# ── GET /security/sessions ────────────────────────────────────────────────────

@router.get(
    "/sessions",
    summary="Active sessions — live session overview with stale and risk signals",
)
def get_active_sessions(
    user_id:     Optional[str] = Query(None,  description="Filter by user UUID"),
    stale_only:  bool          = Query(False, description="Only sessions with last_active > 8 h ago"),
    budget_warn: bool          = Query(False, description="Only sessions with token_budget_status != 'ok'"),
    limit:       int           = Query(100,   ge=1, le=500),
    offset:      int           = Query(0,     ge=0),
    db:     DBSession          = Depends(get_db),
    _admin: User               = Depends(require_admin),
):
    """
    Returns active chat sessions with risk signals.

    **Active Sessions** — all sessions, ordered by last_active desc.
    **Stale Sessions** — last_active > 8 h ago; may indicate orphaned / leaked sessions.
    **Suspicious Activity** — sessions with token_budget_status != 'ok' or
    abnormally high message_count (>500).
    """
    now              = _utcnow()
    stale_cutoff     = now - timedelta(hours=_SESSION_STALE_HOURS)
    stale_naive      = stale_cutoff.replace(tzinfo=None)

    # Summary counters
    total_sessions = db.query(func.count(ChatSession.session_id)).scalar() or 0

    stale_count = (
        db.query(func.count(ChatSession.session_id))
        .filter(ChatSession.last_active < stale_naive)
        .scalar()
    ) or 0

    warned_count = (
        db.query(func.count(ChatSession.session_id))
        .filter(ChatSession.token_budget_status != "ok")
        .scalar()
    ) or 0

    high_volume_count = (
        db.query(func.count(ChatSession.session_id))
        .filter(ChatSession.message_count > 500)
        .scalar()
    ) or 0

    q = db.query(ChatSession)

    if user_id:
        try:
            uid = uuid.UUID(user_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id format")
        q = q.filter(ChatSession.user_id == uid)

    if stale_only:
        q = q.filter(ChatSession.last_active < stale_naive)

    if budget_warn:
        q = q.filter(ChatSession.token_budget_status != "ok")

    total = q.count()
    sessions = (
        q.order_by(ChatSession.last_active.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        # ── Dashboard metrics ──────────────────────────────────
        "total_sessions":    total_sessions,
        "stale_sessions":    stale_count,
        "budget_warned":     warned_count,
        "high_volume":       high_volume_count,
        # ── Paginated records ──────────────────────────────────
        "total":    total,
        "offset":   offset,
        "limit":    limit,
        "sessions": [_session_dict(s, stale_cutoff) for s in sessions],
    }