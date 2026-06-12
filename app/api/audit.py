"""
app/api/audit.py
================
Audit log endpoints.

Org admins can read their own org's audit logs.
Super admin can read privileged_access_logs only — NOT org audit logs.

Routes:
    GET /audit/                          → List audit logs for org (org admin)
    GET /audit/{log_id}                  → Get single audit log entry
    GET /audit/privileged/               → List privileged access logs (super admin)
    POST /audit/privileged/              → Log a privileged access (super admin)
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.db.models_enterprise import AuditLog, PrivilegedAccessLog
from app.services.auth import get_current_user, require_admin, decode_token
from app.services.audit import log_action, log_privileged_access, get_audit_logs
from app.services.rbac import require_super_admin
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

router = APIRouter(prefix="/audit", tags=["Audit Logs"])
bearer_scheme = HTTPBearer(auto_error=True)


# ── Schemas ───────────────────────────────────────────────────────────────────

class PrivilegedAccessRequest(BaseModel):
    org_id:            str  = Field(..., description="Organization being accessed")
    org_name:          str
    resource_accessed: str  = Field(..., description="e.g. usage_logs, audit_logs, users")
    reason:            str  = Field(..., min_length=10, description="Mandatory justification")


def _audit_dict(a: AuditLog) -> dict:
    return {
        "id":              str(a.id),
        "organization_id": str(a.organization_id) if a.organization_id else None,
        "actor_id":        str(a.actor_id)         if a.actor_id        else None,
        "actor_email":     a.actor_email,
        "actor_role":      a.actor_role,
        "action":          a.action,
        "resource_type":   a.resource_type,
        "resource_id":     a.resource_id,
        "resource_name":   a.resource_name,
        "detail":          a.detail,
        "ip_address":      a.ip_address,
        "timestamp":       str(a.timestamp),
    }


def _priv_dict(p: PrivilegedAccessLog) -> dict:
    return {
        "id":                str(p.id),
        "employee_name":     p.employee_name,
        "employee_email":    p.employee_email,
        "role":              p.role,
        "organization_id":   str(p.organization_id) if p.organization_id else None,
        "organization_name": p.organization_name,
        "resource_accessed": p.resource_accessed,
        "reason":            p.reason,
        "ip_address":        p.ip_address,
        "access_granted":    p.access_granted,
        "timestamp":         str(p.timestamp),
    }


# ── GET /audit/ ───────────────────────────────────────────────────────────────

@router.get("/", summary="List audit logs for organization (org admin only)")
def list_audit_logs(
    org_id:  str,
    action:  Optional[str] = None,
    user_id: Optional[str] = None,
    limit:   int = Query(100, ge=1, le=500),
    offset:  int = Query(0,   ge=0),
    db:      Session = Depends(get_db),
    _admin:  User    = Depends(require_admin),
):
    """
    Org admins can read their own org's audit logs only.
    Super admin cannot read this endpoint (privacy model enforced).
    """
    # Prevent super admin from reading org audit logs
    if _admin.role in ("super_admin", "SUPER_ADMIN"):
        raise HTTPException(
            403,
            "Super Admin cannot access organization audit logs. "
            "Use /audit/privileged/ to log your own access instead."
        )

    logs = get_audit_logs(
        db=db, org_id=org_id, user_id=user_id,
        action=action, limit=limit, offset=offset,
    )
    return {
        "total":      len(logs),
        "audit_logs": [_audit_dict(a) for a in logs],
    }


# ── GET /audit/{log_id} ───────────────────────────────────────────────────────

@router.get("/{log_id}", summary="Get single audit log entry")
def get_audit_log(
    log_id: str,
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    if _admin.role in ("super_admin", "SUPER_ADMIN"):
        raise HTTPException(403, "Super Admin cannot access organization audit logs")

    try:
        uid = uuid.UUID(log_id)
    except ValueError:
        raise HTTPException(400, "Invalid log_id")

    log = db.query(AuditLog).filter(AuditLog.id == uid).first()
    if not log:
        raise HTTPException(404, "Audit log not found")

    return _audit_dict(log)


# ── GET /audit/privileged/ ────────────────────────────────────────────────────

@router.get(
    "/privileged/",
    summary="List privileged access logs (super admin only)",
)
def list_privileged_access_logs(
    org_id: Optional[str] = None,
    limit:  int = Query(100, ge=1, le=500),
    offset: int = Query(0,   ge=0),
    db:     Session = Depends(get_db),
    _sa:    dict    = Depends(require_super_admin),
):
    """
    Super admin can see who accessed what customer organization and why.
    This is the compliance trail for privileged access.
    """
    q = db.query(PrivilegedAccessLog)
    if org_id:
        q = q.filter(PrivilegedAccessLog.organization_id == uuid.UUID(org_id))

    total = q.count()
    logs  = q.order_by(PrivilegedAccessLog.timestamp.desc()).limit(limit).offset(offset).all()

    return {
        "total": total,
        "privileged_access_logs": [_priv_dict(p) for p in logs],
    }


# ── POST /audit/privileged/ ───────────────────────────────────────────────────

@router.post(
    "/privileged/",
    status_code=201,
    summary="Log privileged access to customer data (super admin / support)",
)
def record_privileged_access(
    payload: PrivilegedAccessRequest,
    request: Request,
    db:      Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """
    Must be called BEFORE accessing any customer organization data.
    Forces accountability — every access is logged with a reason.
    """
    from app.db.models import User as UserModel
    token_payload = decode_token(credentials.credentials)
    user_id = token_payload.get("sub")
    role    = token_payload.get("role", "")

    if role not in ("super_admin", "SUPER_ADMIN", "admin", "ADMIN"):
        raise HTTPException(403, "Only admins and super admins can log privileged access")

    actor = db.query(UserModel).filter(UserModel.id == user_id).first()
    if not actor:
        raise HTTPException(401, "Actor not found")

    entry = log_privileged_access(
        db                = db,
        employee          = actor,
        org_id            = payload.org_id,
        org_name          = payload.org_name,
        resource_accessed = payload.resource_accessed,
        reason            = payload.reason,
        request           = request,
        access_granted    = True,
    )
    db.commit()

    return {
        "message":   "Privileged access logged",
        "log_id":    str(entry.id),
        "timestamp": str(entry.timestamp),
    }
