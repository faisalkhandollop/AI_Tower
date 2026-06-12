"""
app/api/privacy.py
===================
Phase 5 — Privacy Enforcement API.

Routes
------
GET    /privacy/settings/{org_id}              → get org privacy settings (org admin)
PUT    /privacy/settings/{org_id}              → update org privacy settings (org admin)
GET    /privacy/policies/{org_id}              → list data access policies (org admin)
POST   /privacy/policies/{org_id}              → create data access policy (org admin)
DELETE /privacy/policies/{org_id}/{policy_id}  → delete policy (org admin)
GET    /privacy/export-logs/{org_id}           → list export attempt logs (org admin)
GET    /privacy/super-admin-blocks             → list super_admin block events (super_admin only)
POST   /privacy/settings/{org_id}/reset        → reset settings to platform defaults (org admin)
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.db.models_privacy import (
    CHAT_VISIBILITY_OPTIONS,
    EXPORT_CONTROL_SCOPES,
    DataAccessPolicy,
    ExportControlLog,
    PrivacySettings,
    SuperAdminAccessBlock,
)
from app.services.auth import get_current_user
from app.services.privacy import (
    get_or_create_privacy_settings,
    validate_export_scopes,
    validate_policy_roles,
)
from app.services.rbac import require_org_admin, require_super_admin

router = APIRouter(prefix="/privacy", tags=["Privacy & Data Governance"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class PrivacySettingsResponse(BaseModel):
    id:                              str
    organization_id:                 str
    chat_visibility:                 str
    block_super_admin_conversations: bool
    block_super_admin_messages:      bool
    block_super_admin_attachments:   bool
    block_super_admin_kb:            bool
    block_super_admin_api_keys:      bool
    exports_enabled:                 bool
    allowed_export_scopes:           list[str]
    export_requires_reason:          bool
    export_notify_org_admin:         bool
    message_retention_days:          int
    attachment_retention_days:       int
    usage_log_retention_days:        int
    updated_at:                      str


class UpdatePrivacySettingsRequest(BaseModel):
    chat_visibility:            Optional[str] = None
    exports_enabled:            Optional[bool] = None
    allowed_export_scopes:      Optional[list[str]] = None
    export_requires_reason:     Optional[bool] = None
    export_notify_org_admin:    Optional[bool] = None
    message_retention_days:     Optional[int] = Field(None, ge=0)
    attachment_retention_days:  Optional[int] = Field(None, ge=0)
    usage_log_retention_days:   Optional[int] = Field(None, ge=0)

    @field_validator("chat_visibility")
    @classmethod
    def validate_visibility(cls, v):
        if v is not None and v not in CHAT_VISIBILITY_OPTIONS:
            raise ValueError(
                f"chat_visibility must be one of: {list(CHAT_VISIBILITY_OPTIONS)}"
            )
        return v

    @field_validator("allowed_export_scopes")
    @classmethod
    def validate_scopes(cls, v):
        if v is not None:
            validate_export_scopes(v)
        return v


class DataAccessPolicyCreate(BaseModel):
    name:                     str = Field(..., max_length=200)
    description:              Optional[str] = None
    permitted_data_categories: list[str]
    allowed_roles:            list[str]

    @field_validator("allowed_roles")
    @classmethod
    def no_super_admin(cls, v):
        validate_policy_roles(v)
        return v

    @field_validator("permitted_data_categories")
    @classmethod
    def validate_categories(cls, v):
        from app.db.models_privacy import DATA_CATEGORIES
        invalid = [c for c in v if c not in DATA_CATEGORIES]
        if invalid:
            raise ValueError(f"Unknown data categories: {invalid}")
        return v


class DataAccessPolicyResponse(BaseModel):
    id:                        str
    organization_id:           str
    name:                      str
    description:               Optional[str]
    permitted_data_categories: list[str]
    allowed_roles:             list[str]
    is_active:                 bool
    created_at:                str


class ExportLogResponse(BaseModel):
    id:             str
    organization_id: str
    requester_role: str
    requester_email: Optional[str]
    scope:          str
    export_format:  str
    reason:         Optional[str]
    decision:       str
    block_reason:   Optional[str]
    record_count:   Optional[int]
    ip_address:     Optional[str]
    timestamp:      str


class SuperAdminBlockResponse(BaseModel):
    id:                  str
    super_admin_email:   Optional[str]
    organization_name:   Optional[str]
    attempted_resource:  str
    endpoint_path:       Optional[str]
    http_method:         Optional[str]
    block_reason:        str
    ip_address:          Optional[str]
    timestamp:           str


# ── Serialisers ───────────────────────────────────────────────────────────────

def _settings_dict(s: PrivacySettings) -> dict:
    return {
        "id":                              str(s.id),
        "organization_id":                 str(s.organization_id),
        "chat_visibility":                 s.chat_visibility,
        "block_super_admin_conversations": s.block_super_admin_conversations,
        "block_super_admin_messages":      s.block_super_admin_messages,
        "block_super_admin_attachments":   s.block_super_admin_attachments,
        "block_super_admin_kb":            s.block_super_admin_kb,
        "block_super_admin_api_keys":      s.block_super_admin_api_keys,
        "exports_enabled":                 s.exports_enabled,
        "allowed_export_scopes":           s.allowed_export_scopes or [],
        "export_requires_reason":          s.export_requires_reason,
        "export_notify_org_admin":         s.export_notify_org_admin,
        "message_retention_days":          s.message_retention_days,
        "attachment_retention_days":       s.attachment_retention_days,
        "usage_log_retention_days":        s.usage_log_retention_days,
        "updated_at":                      str(s.updated_at),
    }


def _policy_dict(p: DataAccessPolicy) -> dict:
    return {
        "id":                        str(p.id),
        "organization_id":           str(p.organization_id),
        "name":                      p.name,
        "description":               p.description,
        "permitted_data_categories": p.permitted_data_categories or [],
        "allowed_roles":             p.allowed_roles or [],
        "is_active":                 p.is_active,
        "created_at":                str(p.created_at),
    }


def _export_log_dict(e: ExportControlLog) -> dict:
    return {
        "id":              str(e.id),
        "organization_id": str(e.organization_id) if e.organization_id else None,
        "requester_role":  e.requester_role,
        "requester_email": e.requester_email,
        "scope":           e.scope,
        "export_format":   e.export_format,
        "reason":          e.reason,
        "decision":        e.decision,
        "block_reason":    e.block_reason,
        "record_count":    e.record_count,
        "ip_address":      e.ip_address,
        "timestamp":       str(e.timestamp),
    }


def _block_dict(b: SuperAdminAccessBlock) -> dict:
    return {
        "id":                 str(b.id),
        "super_admin_email":  b.super_admin_email,
        "organization_name":  b.organization_name,
        "attempted_resource": b.attempted_resource,
        "endpoint_path":      b.endpoint_path,
        "http_method":        b.http_method,
        "block_reason":       b.block_reason,
        "ip_address":         b.ip_address,
        "timestamp":          str(b.timestamp),
    }


# ── Routes ────────────────────────────────────────────────────────────────────

# GET /privacy/settings/{org_id}
@router.get(
    "/settings/{org_id}",
    summary="Get organization privacy settings",
    response_model=PrivacySettingsResponse,
)
def get_privacy_settings(
    org_id:       str,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    """
    Returns the privacy settings for the given org.
    Creates default settings on first access.
    ORG_ADMIN only — SUPER_ADMIN cannot view these to prevent circumventing them.
    """
    settings = get_or_create_privacy_settings(db, org_id)
    db.commit()
    return _settings_dict(settings)


# PUT /privacy/settings/{org_id}
@router.put(
    "/settings/{org_id}",
    summary="Update organization privacy settings",
    response_model=PrivacySettingsResponse,
)
def update_privacy_settings(
    org_id:       str,
    payload:      UpdatePrivacySettingsRequest,
    request:      Request,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    """
    Update mutable privacy settings for an org.

    Note: The super_admin block flags are NOT updatable through this endpoint —
    they are platform-enforced invariants and always remain True.
    """
    settings = get_or_create_privacy_settings(db, org_id)

    if payload.chat_visibility is not None:
        settings.chat_visibility = payload.chat_visibility
    if payload.exports_enabled is not None:
        settings.exports_enabled = payload.exports_enabled
    if payload.allowed_export_scopes is not None:
        settings.allowed_export_scopes = payload.allowed_export_scopes
    if payload.export_requires_reason is not None:
        settings.export_requires_reason = payload.export_requires_reason
    if payload.export_notify_org_admin is not None:
        settings.export_notify_org_admin = payload.export_notify_org_admin
    if payload.message_retention_days is not None:
        settings.message_retention_days = payload.message_retention_days
    if payload.attachment_retention_days is not None:
        settings.attachment_retention_days = payload.attachment_retention_days
    if payload.usage_log_retention_days is not None:
        settings.usage_log_retention_days = payload.usage_log_retention_days

    settings.last_updated_by = current_user.id

    # Enforce immutability of super_admin blocks — always re-set to True.
    settings.block_super_admin_conversations = True
    settings.block_super_admin_messages      = True
    settings.block_super_admin_attachments   = True
    settings.block_super_admin_kb            = True
    settings.block_super_admin_api_keys      = True

    db.commit()
    db.refresh(settings)
    return _settings_dict(settings)


# POST /privacy/settings/{org_id}/reset
@router.post(
    "/settings/{org_id}/reset",
    summary="Reset privacy settings to platform defaults",
)
def reset_privacy_settings(
    org_id:       str,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    """
    Wipe org-level customisations and restore platform defaults.
    """
    try:
        oid = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(400, "Invalid org_id")

    existing = (
        db.query(PrivacySettings)
        .filter(PrivacySettings.organization_id == oid)
        .first()
    )
    if existing:
        db.delete(existing)
        db.flush()

    # Re-create with defaults
    new = PrivacySettings(
        organization_id=oid,
        last_updated_by=current_user.id,
    )
    db.add(new)
    db.commit()
    db.refresh(new)
    return {"message": "Privacy settings reset to defaults.", "settings": _settings_dict(new)}


# ── Data Access Policies ──────────────────────────────────────────────────────

# GET /privacy/policies/{org_id}
@router.get(
    "/policies/{org_id}",
    summary="List data access policies for org",
)
def list_policies(
    org_id:       str,
    active_only:  bool = True,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    try:
        oid = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(400, "Invalid org_id")

    q = db.query(DataAccessPolicy).filter(DataAccessPolicy.organization_id == oid)
    if active_only:
        q = q.filter(DataAccessPolicy.is_active == True)

    policies = q.order_by(DataAccessPolicy.created_at.asc()).all()
    return {"total": len(policies), "policies": [_policy_dict(p) for p in policies]}


# POST /privacy/policies/{org_id}
@router.post(
    "/policies/{org_id}",
    status_code=201,
    summary="Create a data access policy",
)
def create_policy(
    org_id:       str,
    payload:      DataAccessPolicyCreate,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
    _perm:        dict    = Depends(require_org_admin),
):
    try:
        oid = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(400, "Invalid org_id")

    # Check uniqueness
    existing = (
        db.query(DataAccessPolicy)
        .filter(
            DataAccessPolicy.organization_id == oid,
            DataAccessPolicy.name == payload.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"A policy named '{payload.name}' already exists.")

    policy = DataAccessPolicy(
        organization_id           = oid,
        name                      = payload.name,
        description               = payload.description,
        permitted_data_categories = payload.permitted_data_categories,
        allowed_roles             = payload.allowed_roles,
        created_by                = current_user.id,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return {"policy": _policy_dict(policy)}


# DELETE /privacy/policies/{org_id}/{policy_id}
@router.delete(
    "/policies/{org_id}/{policy_id}",
    summary="Deactivate (soft-delete) a data access policy",
)
def delete_policy(
    org_id:    str,
    policy_id: str,
    db:        Session = Depends(get_db),
    _perm:     dict    = Depends(require_org_admin),
):
    try:
        oid = uuid.UUID(org_id)
        pid = uuid.UUID(policy_id)
    except ValueError:
        raise HTTPException(400, "Invalid org_id or policy_id")

    policy = (
        db.query(DataAccessPolicy)
        .filter(
            DataAccessPolicy.id == pid,
            DataAccessPolicy.organization_id == oid,
        )
        .first()
    )
    if not policy:
        raise HTTPException(404, "Policy not found")

    policy.is_active = False
    db.commit()
    return {"message": "Policy deactivated."}


# ── Export Logs ───────────────────────────────────────────────────────────────

# GET /privacy/export-logs/{org_id}
@router.get(
    "/export-logs/{org_id}",
    summary="List export attempt logs for org (org admin only)",
)
def list_export_logs(
    org_id:   str,
    decision: Optional[str] = None,   # "allowed" | "blocked"
    limit:    int = 50,
    offset:   int = 0,
    db:       Session = Depends(get_db),
    _perm:    dict    = Depends(require_org_admin),
):
    try:
        oid = uuid.UUID(org_id)
    except ValueError:
        raise HTTPException(400, "Invalid org_id")

    q = db.query(ExportControlLog).filter(ExportControlLog.organization_id == oid)
    if decision:
        q = q.filter(ExportControlLog.decision == decision)

    total = q.count()
    logs  = (
        q.order_by(ExportControlLog.timestamp.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {
        "total": total,
        "logs":  [_export_log_dict(e) for e in logs],
    }


# ── Super Admin Block Log ─────────────────────────────────────────────────────

# GET /privacy/super-admin-blocks
@router.get(
    "/super-admin-blocks",
    summary="List super admin access block events (super admin only)",
)
def list_super_admin_blocks(
    org_id:   Optional[str] = None,
    limit:    int = 100,
    offset:   int = 0,
    db:       Session = Depends(get_db),
    _perm:    dict    = Depends(require_super_admin),
):
    """
    Super admin can see a list of their own (or all) blocked access attempts.
    This endpoint is intentionally available to super admins for self-auditing.
    It does NOT expose any customer content — only the metadata of blocked attempts.
    """
    q = db.query(SuperAdminAccessBlock)
    if org_id:
        try:
            oid = uuid.UUID(org_id)
            q = q.filter(SuperAdminAccessBlock.organization_id == oid)
        except ValueError:
            raise HTTPException(400, "Invalid org_id")

    total  = q.count()
    blocks = (
        q.order_by(SuperAdminAccessBlock.timestamp.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {
        "total":  total,
        "blocks": [_block_dict(b) for b in blocks],
    }