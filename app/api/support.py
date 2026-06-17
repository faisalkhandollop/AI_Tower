"""
app/api/support.py
====================
Phase 6 — Support Access Workflow API.

Routes
------
POST /support/request   → customer (ORG_ADMIN+) raises a support access request
POST /support/approve   → platform admin (SUPER_ADMIN) approves or denies a request
POST /support/revoke    → revoke an active/pending grant (customer org admin or platform admin)
GET  /support/history   → immutable audit log of request lifecycle + resource access

Workflow
--------
Customer Raises Ticket
    ↓
Access Request          (POST /support/request, status=pending)
    ↓
Approval                 (POST /support/approve, status=approved|denied)
    ↓
Temporary Access         (scoped JWT grant, status=approved until expires_at)
    ↓
Audit Log                (GET /support/history, immutable)
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User, UserTeam
from app.db.models_support import SupportAccessLog, SupportAccessRequest
from app.schemas.support_access import (
    SupportAccessApprove,
    SupportAccessApproveResponse,
    SupportAccessHistoryResponse,
    SupportAccessLogResponse,
    SupportAccessRequestCreate,
    SupportAccessRequestResponse,
    SupportAccessRevoke,
)
from app.services.auth import get_current_user
from app.services.rbac import require_org_admin, require_super_admin
from app.services.support_access import (
    clamp_minutes,
    compute_expiry,
    create_access_grant_token,
    expire_stale_requests,
    log_support_access_event,
    validate_scopes,
)

router = APIRouter(prefix="/support", tags=["Support Access Workflow"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_user_org_id(db: Session, user: User) -> Optional[uuid.UUID]:
    """Resolve a user's organization via their UserTeam membership."""
    membership = db.query(UserTeam).filter(UserTeam.user_id == user.id).first()
    return membership.org_id if membership else None


# ── Serialisers ───────────────────────────────────────────────────────────────

def _request_dict(r: SupportAccessRequest) -> dict:
    return {
        "id":                str(r.id),
        "organization_id":   str(r.organization_id),
        "ticket_reference":  r.ticket_reference,
        "reason":            r.reason,
        "requested_by":      str(r.requested_by),
        "requester_email":   r.requester_email,
        "requester_role":    r.requester_role,
        "requested_scopes":  r.requested_scopes or [],
        "requested_minutes": r.requested_minutes,
        "status":            r.status,
        "reviewed_by":       str(r.reviewed_by) if r.reviewed_by else None,
        "reviewer_email":    r.reviewer_email,
        "review_notes":      r.review_notes,
        "reviewed_at":       str(r.reviewed_at) if r.reviewed_at else None,
        "granted_scopes":    r.granted_scopes,
        "granted_to":        str(r.granted_to) if r.granted_to else None,
        "grantee_email":     r.grantee_email,
        "expires_at":        str(r.expires_at) if r.expires_at else None,
        "revoked_by":        str(r.revoked_by) if r.revoked_by else None,
        "revoked_at":        str(r.revoked_at) if r.revoked_at else None,
        "revoke_reason":     r.revoke_reason,
        "created_at":        str(r.created_at),
        "updated_at":        str(r.updated_at),
    }


def _log_dict(e: SupportAccessLog) -> dict:
    return {
        "id":              str(e.id),
        "request_id":      str(e.request_id),
        "organization_id": str(e.organization_id),
        "action":          e.action,
        "actor_id":        str(e.actor_id) if e.actor_id else None,
        "actor_email":     e.actor_email,
        "actor_role":      e.actor_role,
        "resource_type":   e.resource_type,
        "resource_id":     e.resource_id,
        "endpoint_path":   e.endpoint_path,
        "http_method":     e.http_method,
        "detail":          e.detail,
        "ip_address":      e.ip_address,
        "timestamp":       str(e.timestamp),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

# POST /support/request
@router.post(
    "/request",
    status_code=201,
    summary="Raise a support access request (customer org admin)",
    response_model=SupportAccessRequestResponse,
)
def create_support_access_request(
    payload:      SupportAccessRequestCreate,
    request:      Request,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    """
    Customer-side entry point. An ORG_ADMIN (or SUPER_ADMIN, for platform-
    initiated tickets) raises a request for temporary support access to
    their organization's data.

    Always created with status="pending" — no access is granted until a
    platform admin approves via POST /support/approve.
    """
    try:
        org_id = uuid.UUID(payload.organization_id)
    except ValueError:
        raise HTTPException(400, "Invalid organization_id")

    validate_scopes(payload.requested_scopes)

    req = SupportAccessRequest(
        organization_id   = org_id,
        ticket_reference  = payload.ticket_reference,
        reason            = payload.reason,
        requested_by      = current_user.id,
        requester_email   = current_user.email,
        requester_role    = current_user.role,
        requested_scopes  = payload.requested_scopes,
        requested_minutes = clamp_minutes(payload.requested_minutes),
        status            = "pending",
    )
    db.add(req)
    db.flush()

    log_support_access_event(
        db, req, "REQUEST_CREATED", current_user,
        detail={
            "ticket_reference": payload.ticket_reference,
            "requested_scopes": payload.requested_scopes,
            "requested_minutes": req.requested_minutes,
        },
        request=request,
    )

    db.commit()
    db.refresh(req)
    return _request_dict(req)


# POST /support/approve
@router.post(
    "/approve",
    summary="Approve or deny a pending support access request (platform admin)",
    response_model=SupportAccessApproveResponse,
)
def approve_support_access_request(
    payload:      SupportAccessApprove,
    request:      Request,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_super_admin),
):
    """
    Platform-side approval gate. Only SUPER_ADMIN may approve or deny.

    On approve:
      - granted_scopes defaults to the full requested_scopes if not
        explicitly narrowed by the reviewer.
      - granted_to defaults to the reviewer (current_user) if not specified.
      - A short-lived scoped JWT ("support access grant") is minted, valid
        until expires_at = now + grant_minutes (or requested_minutes,
        clamped to MAX_GRANT_MINUTES).
      - status becomes "approved".

    On deny:
      - status becomes "denied". No token is issued.
    """
    try:
        req_id = uuid.UUID(payload.request_id)
    except ValueError:
        raise HTTPException(400, "Invalid request_id")

    req = db.query(SupportAccessRequest).filter(SupportAccessRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Support access request not found")

    if req.status != "pending":
        raise HTTPException(409, f"Request is already '{req.status}' — cannot be reviewed again")

    req.reviewed_by    = current_user.id
    req.reviewer_email = current_user.email
    req.review_notes   = payload.review_notes
    req.reviewed_at    = datetime.now(timezone.utc)

    if payload.decision == "deny":
        req.status = "denied"
        db.flush()
        log_support_access_event(
            db, req, "REQUEST_DENIED", current_user,
            detail={"review_notes": payload.review_notes},
            request=request,
        )
        db.commit()
        db.refresh(req)
        return {"request": _request_dict(req), "access_token": None}

    # decision == "approve"
    granted_scopes = payload.granted_scopes or req.requested_scopes
    validate_scopes(granted_scopes)

    grantee = current_user
    if payload.granted_to:
        try:
            grantee_id = uuid.UUID(payload.granted_to)
        except ValueError:
            raise HTTPException(400, "Invalid granted_to user id")
        grantee = db.query(User).filter(User.id == grantee_id).first()
        if not grantee:
            raise HTTPException(404, "granted_to user not found")

    grant_minutes = payload.grant_minutes or req.requested_minutes
    req.granted_scopes = granted_scopes
    req.granted_to     = grantee.id
    req.grantee_email  = grantee.email
    req.expires_at     = compute_expiry(grant_minutes)
    req.status         = "approved"
    db.flush()

    log_support_access_event(
        db, req, "REQUEST_APPROVED", current_user,
        detail={
            "granted_scopes": granted_scopes,
            "granted_to":     str(grantee.id),
            "grant_minutes":  clamp_minutes(grant_minutes),
            "expires_at":     str(req.expires_at),
            "review_notes":   payload.review_notes,
        },
        request=request,
    )

    token, jti = create_access_grant_token(req, grantee)
    req.access_token_jti = jti
    db.flush()

    log_support_access_event(
        db, req, "ACCESS_GRANTED", current_user,
        detail={"jti": jti, "expires_at": str(req.expires_at)},
        request=request,
    )

    db.commit()
    db.refresh(req)
    return {"request": _request_dict(req), "access_token": token}


# POST /support/revoke
@router.post(
    "/revoke",
    summary="Revoke a pending or active support access grant",
    response_model=SupportAccessRequestResponse,
)
def revoke_support_access_request(
    payload:      SupportAccessRevoke,
    request:      Request,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    """
    Revoke a support access request. May be called by:
      - the customer org admin who owns the affected organization, or
      - a SUPER_ADMIN (platform admin).

    Valid from status "pending" or "approved" → "revoked". Any previously
    issued grant token is immediately invalidated (status check in
    enforce_support_access rejects non-"approved" requests).
    """
    try:
        req_id = uuid.UUID(payload.request_id)
    except ValueError:
        raise HTTPException(400, "Invalid request_id")

    req = db.query(SupportAccessRequest).filter(SupportAccessRequest.id == req_id).first()
    if not req:
        raise HTTPException(404, "Support access request not found")

    role = (current_user.role or "").upper()
    is_super_admin = role in {"SUPER_ADMIN"}
    user_org_id = _resolve_user_org_id(db, current_user)
    is_org_owner = user_org_id is not None and str(req.organization_id) == str(user_org_id)

    if not is_super_admin and not is_org_owner:
        raise HTTPException(403, "Not authorized to revoke this support access request")

    if req.status not in ("pending", "approved"):
        raise HTTPException(409, f"Request is already '{req.status}' — cannot be revoked")

    req.status       = "revoked"
    req.revoked_by   = current_user.id
    req.revoked_at   = datetime.now(timezone.utc)
    req.revoke_reason = payload.reason
    db.flush()

    log_support_access_event(
        db, req, "ACCESS_REVOKED", current_user,
        detail={"reason": payload.reason},
        request=request,
    )

    db.commit()
    db.refresh(req)
    return _request_dict(req)


# GET /support/history
@router.get(
    "/history",
    summary="List support access audit log entries",
    response_model=SupportAccessHistoryResponse,
)
def get_support_access_history(
    organization_id: Optional[str] = None,
    request_id:      Optional[str] = None,
    action:          Optional[str] = None,
    limit:           int = 100,
    offset:          int = 0,
    db:              Session = Depends(get_db),
    current_user:    User    = Depends(get_current_user),
    _perm:           dict    = Depends(require_org_admin),
):
    """
    Immutable audit trail of support access lifecycle events and resource
    accesses. ORG_ADMIN may view rows for their own organization; SUPER_ADMIN
    may view any organization (filterable via organization_id).
    """
    # Lazily expire any approved grants whose window has elapsed.
    expire_stale_requests(db)

    q = db.query(SupportAccessLog)

    role = (current_user.role or "").upper()
    if role != "SUPER_ADMIN":
        own_org = _resolve_user_org_id(db, current_user)
        if not own_org:
            raise HTTPException(403, "No organization context for this user")
        q = q.filter(SupportAccessLog.organization_id == own_org)
    elif organization_id:
        try:
            oid = uuid.UUID(organization_id)
        except ValueError:
            raise HTTPException(400, "Invalid organization_id")
        q = q.filter(SupportAccessLog.organization_id == oid)

    if request_id:
        try:
            rid = uuid.UUID(request_id)
        except ValueError:
            raise HTTPException(400, "Invalid request_id")
        q = q.filter(SupportAccessLog.request_id == rid)

    if action:
        q = q.filter(SupportAccessLog.action == action)

    total = q.count()
    logs = (
        q.order_by(SupportAccessLog.timestamp.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {"total": total, "logs": [_log_dict(e) for e in logs]}