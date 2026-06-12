"""
app/services/audit.py
=====================
Audit logging service.

Writes immutable audit_log rows automatically.
Called explicitly from API endpoints after every state-changing operation.

Also writes privileged_access_logs when support/super_admin
accesses organization data.

Usage:
    from app.services.audit import log_action, log_privileged_access

    # In an endpoint:
    log_action(
        db         = db,
        action     = "USER_CREATED",
        actor      = current_user,
        org_id     = org_id,
        resource_type = "user",
        resource_id   = str(new_user.id),
        resource_name = new_user.email,
        detail        = {"role": new_user.role},
        request       = request,   # FastAPI Request — for IP/user-agent
    )
"""

import json
import uuid
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import User
from app.db.models_enterprise import AuditLog, PrivilegedAccessLog


def log_action(
    db:            Session,
    action:        str,
    actor:         Optional[User],
    org_id:        Optional[str]   = None,
    resource_type: Optional[str]   = None,
    resource_id:   Optional[str]   = None,
    resource_name: Optional[str]   = None,
    detail:        Optional[dict]  = None,
    request:       Optional[Request] = None,
) -> AuditLog:
    """
    Write an immutable audit log entry.

    Parameters
    ----------
    db            : SQLAlchemy session
    action        : One of AUDIT_ACTIONS (e.g. "USER_CREATED")
    actor         : The User performing the action (None for system actions)
    org_id        : UUID string of the affected organization
    resource_type : Type of resource affected (e.g. "user", "team", "api_key")
    resource_id   : UUID string of the affected resource
    resource_name : Human-readable name of the resource
    detail        : Dict of changed fields or context (serialized to JSON)
    request       : FastAPI Request object for IP + user agent extraction
    """
    ip_address = None
    user_agent = None

    if request:
        # Respect X-Forwarded-For for proxied requests
        forwarded_for = request.headers.get("X-Forwarded-For")
        ip_address = (
            forwarded_for.split(",")[0].strip()
            if forwarded_for
            else request.client.host if request.client else None
        )
        user_agent = request.headers.get("User-Agent")

    entry = AuditLog(
        organization_id = uuid.UUID(org_id) if org_id else None,
        actor_id        = actor.id          if actor  else None,
        actor_email     = actor.email       if actor  else None,
        actor_role      = actor.role        if actor  else "system",
        action          = action,
        resource_type   = resource_type,
        resource_id     = resource_id,
        resource_name   = resource_name,
        detail          = json.dumps(detail, default=str) if detail else None,
        ip_address      = ip_address,
        user_agent      = user_agent,
    )

    db.add(entry)
    db.flush()   # flush but don't commit — caller controls transaction
    return entry


def log_privileged_access(
    db:                Session,
    employee:          User,
    org_id:            Optional[str],
    org_name:          Optional[str],
    resource_accessed: str,
    reason:            str,
    request:           Optional[Request] = None,
    access_granted:    bool = True,
) -> PrivilegedAccessLog:
    """
    Log a support/admin access to customer organization data.

    This should be called whenever:
    - A SUPER_ADMIN reads usage metrics scoped to an org
    - A support engineer views customer-specific data
    - Any privileged user accesses data outside their normal org scope

    Parameters
    ----------
    employee          : The User performing the privileged access
    org_id            : UUID string of the organization being accessed
    org_name          : Name of the organization (denormalized for audit trail)
    resource_accessed : What was accessed (e.g. "usage_logs", "audit_logs")
    reason            : Mandatory justification for the access
    access_granted    : Whether access was actually granted (False = blocked attempt)
    """
    ip_address = None
    if request and request.client:
        ip_address = request.headers.get("X-Forwarded-For", request.client.host)

    entry = PrivilegedAccessLog(
        employee_id       = employee.id,
        employee_name     = employee.name,
        employee_email    = employee.email,
        role              = employee.role,
        organization_id   = uuid.UUID(org_id) if org_id else None,
        organization_name = org_name,
        resource_accessed = resource_accessed,
        reason            = reason,
        ip_address        = ip_address,
        access_granted    = access_granted,
    )

    db.add(entry)
    db.flush()
    return entry


def get_audit_logs(
    db:      Session,
    org_id:  Optional[str] = None,
    user_id: Optional[str] = None,
    action:  Optional[str] = None,
    limit:   int = 100,
    offset:  int = 0,
) -> list[AuditLog]:
    """
    Query audit logs with optional filters.
    Results ordered by timestamp descending (newest first).
    """
    q = db.query(AuditLog)
    if org_id:
        q = q.filter(AuditLog.organization_id == uuid.UUID(org_id))
    if user_id:
        q = q.filter(AuditLog.actor_id == uuid.UUID(user_id))
    if action:
        q = q.filter(AuditLog.action == action)
    return (
        q.order_by(AuditLog.timestamp.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
