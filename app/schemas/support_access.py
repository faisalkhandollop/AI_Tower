"""
app/schemas/support_access.py
===============================
Pydantic request / response schemas for Phase 6 Support Access Workflow.

Covers:
    SupportAccessRequestCreate  — customer raises a request (POST /support/request)
    SupportAccessApprove        — admin approves a pending request (POST /support/approve)
    SupportAccessDeny           — admin denies a pending request   (POST /support/approve, decision=deny)
    SupportAccessRevoke         — revoke an active grant            (POST /support/revoke)
    SupportAccessRequestResponse — read model for a request
    SupportAccessLogResponse    — read model for an audit log row
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.db.models_support import SUPPORT_ACCESS_SCOPES


# ═════════════════════════════════════════════════════════════
# REQUEST CREATION (customer side)
# ═════════════════════════════════════════════════════════════

class SupportAccessRequestCreate(BaseModel):
    organization_id:  str
    ticket_reference: Optional[str] = Field(None, max_length=200)
    reason:           str = Field(..., min_length=1)
    requested_scopes: list[str] = Field(..., min_length=1)
    requested_minutes: int = Field(60, gt=0, le=480)

    @field_validator("requested_scopes")
    @classmethod
    def validate_scopes(cls, v):
        invalid = [s for s in v if s not in SUPPORT_ACCESS_SCOPES]
        if invalid:
            raise ValueError(
                f"Unknown support access scopes: {invalid}. "
                f"Valid scopes: {list(SUPPORT_ACCESS_SCOPES)}"
            )
        return v


# ═════════════════════════════════════════════════════════════
# APPROVAL / DENIAL (platform admin side)
# ═════════════════════════════════════════════════════════════

class SupportAccessApprove(BaseModel):
    request_id:     str
    decision:       str = Field(..., pattern=r"^(approve|deny)$")
    granted_scopes: Optional[list[str]] = None   # required if decision == "approve"
    granted_to:     Optional[str] = None         # support engineer user id; defaults to reviewer
    grant_minutes:  Optional[int] = Field(None, gt=0, le=480)
    review_notes:   Optional[str] = None

    @field_validator("granted_scopes")
    @classmethod
    def validate_granted_scopes(cls, v):
        if v is not None:
            invalid = [s for s in v if s not in SUPPORT_ACCESS_SCOPES]
            if invalid:
                raise ValueError(
                    f"Unknown support access scopes: {invalid}. "
                    f"Valid scopes: {list(SUPPORT_ACCESS_SCOPES)}"
                )
        return v


# ═════════════════════════════════════════════════════════════
# REVOCATION
# ═════════════════════════════════════════════════════════════

class SupportAccessRevoke(BaseModel):
    request_id: str
    reason:     Optional[str] = None


# ═════════════════════════════════════════════════════════════
# READ MODELS
# ═════════════════════════════════════════════════════════════

class SupportAccessRequestResponse(BaseModel):
    id:                str
    organization_id:   str
    ticket_reference:  Optional[str]
    reason:            str
    requested_by:      str
    requester_email:   Optional[str]
    requester_role:    Optional[str]
    requested_scopes:  list[str]
    requested_minutes: int
    status:            str
    reviewed_by:       Optional[str]
    reviewer_email:    Optional[str]
    review_notes:      Optional[str]
    reviewed_at:       Optional[str]
    granted_scopes:    Optional[list[str]]
    granted_to:        Optional[str]
    grantee_email:     Optional[str]
    expires_at:        Optional[str]
    revoked_by:        Optional[str]
    revoked_at:        Optional[str]
    revoke_reason:     Optional[str]
    created_at:        str
    updated_at:        str


class SupportAccessApproveResponse(BaseModel):
    request: SupportAccessRequestResponse
    access_token: Optional[str] = None   # only present on approval


class SupportAccessLogResponse(BaseModel):
    id:              str
    request_id:      str
    organization_id: str
    action:          str
    actor_id:        Optional[str]
    actor_email:     Optional[str]
    actor_role:      Optional[str]
    resource_type:   Optional[str]
    resource_id:     Optional[str]
    endpoint_path:   Optional[str]
    http_method:     Optional[str]
    detail:          Optional[str]
    ip_address:      Optional[str]
    timestamp:       str


class SupportAccessHistoryResponse(BaseModel):
    total: int
    logs:  list[SupportAccessLogResponse]