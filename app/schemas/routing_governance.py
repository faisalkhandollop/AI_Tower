"""
app/schemas/routing_governance.py
==================================
Pydantic request / response schemas for Phase 3 Routing Governance.

Covers:
    RoutingRule    — CRUD
    RoutingLog     — read-only
    RoutingFeedback — submit & list
    PromptCategory — CRUD
    Analytics      — dashboard aggregates
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


# ═════════════════════════════════════════════════════════════
# PROMPT CATEGORIES
# ═════════════════════════════════════════════════════════════

class PromptCategoryCreate(BaseModel):
    name:        str          = Field(..., max_length=100)
    slug:        str          = Field(..., max_length=100, pattern=r"^[a-z0-9_]+$")
    description: Optional[str] = None
    color_hex:   Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    is_active:   bool          = True


class PromptCategoryUpdate(BaseModel):
    name:        Optional[str]  = None
    description: Optional[str]  = None
    color_hex:   Optional[str]  = None
    is_active:   Optional[bool] = None


class PromptCategoryOut(BaseModel):
    id:          uuid.UUID
    name:        str
    slug:        str
    description: Optional[str]
    color_hex:   Optional[str]
    is_active:   bool
    created_at:  datetime

    class Config:
        from_attributes = True


# ═════════════════════════════════════════════════════════════
# ROUTING RULES
# ═════════════════════════════════════════════════════════════

class RoutingRuleCreate(BaseModel):
    name:             str            = Field(..., max_length=200)
    description:      Optional[str]  = None
    organization_id:  Optional[uuid.UUID] = None
    department_id:    Optional[uuid.UUID] = None

    # Match
    match_category:    Optional[str]   = None
    match_complexity:  Optional[str]   = Field(None, pattern=r"^(simple|medium|complex)$")
    match_provider:    Optional[str]   = None
    match_model:       Optional[str]   = None
    cost_threshold_usd:Optional[float] = None

    # Action
    action:           str  = Field(..., pattern=r"^(force_provider|force_model|block|fallback|cost_cap)$")
    target_provider:  Optional[str]  = None
    target_model:     Optional[str]  = None
    fallback_provider:Optional[str]  = None
    fallback_model:   Optional[str]  = None

    # Governance
    priority:         int  = 100
    is_active:        bool = True


class RoutingRuleUpdate(BaseModel):
    name:             Optional[str]   = None
    description:      Optional[str]   = None
    match_category:   Optional[str]   = None
    match_complexity: Optional[str]   = None
    match_provider:   Optional[str]   = None
    match_model:      Optional[str]   = None
    cost_threshold_usd:Optional[float]= None
    action:           Optional[str]   = None
    target_provider:  Optional[str]   = None
    target_model:     Optional[str]   = None
    fallback_provider:Optional[str]   = None
    fallback_model:   Optional[str]   = None
    priority:         Optional[int]   = None
    is_active:        Optional[bool]  = None


class RoutingRuleOut(BaseModel):
    id:               uuid.UUID
    organization_id:  Optional[uuid.UUID]
    department_id:    Optional[uuid.UUID]
    name:             str
    description:      Optional[str]
    match_category:   Optional[str]
    match_complexity: Optional[str]
    match_provider:   Optional[str]
    match_model:      Optional[str]
    cost_threshold_usd: Optional[float]
    action:           str
    target_provider:  Optional[str]
    target_model:     Optional[str]
    fallback_provider:Optional[str]
    fallback_model:   Optional[str]
    priority:         int
    is_active:        bool
    created_by:       Optional[uuid.UUID]
    created_at:       datetime
    updated_at:       datetime

    class Config:
        from_attributes = True


# ═════════════════════════════════════════════════════════════
# ROUTING LOGS
# ═════════════════════════════════════════════════════════════

class RoutingLogOut(BaseModel):
    id:               uuid.UUID
    organization_id:  Optional[uuid.UUID]
    user_id:          Optional[uuid.UUID]
    conversation_id:  Optional[uuid.UUID]
    session_id:       Optional[str]
    prompt_preview:   Optional[str]
    prompt_length:    Optional[int]
    category_slug:    Optional[str]
    complexity:       Optional[str]
    complexity_score: Optional[int]
    department:       Optional[str]
    router_engine:    Optional[str]
    rule_id:          Optional[uuid.UUID]
    selected_provider:str
    selected_model:   Optional[str]
    was_fallback:     bool
    fallback_reason:  Optional[str]
    original_provider:Optional[str]
    original_model:   Optional[str]
    input_tokens:     Optional[int]
    output_tokens:    Optional[int]
    total_tokens:     Optional[int]
    cost_usd:         Optional[float]
    latency_ms:       Optional[int]
    status:           Optional[str]
    error_message:    Optional[str]
    created_at:       datetime

    class Config:
        from_attributes = True


class RoutingLogListResponse(BaseModel):
    total:  int
    page:   int
    limit:  int
    items:  List[RoutingLogOut]


# ═════════════════════════════════════════════════════════════
# ROUTING FEEDBACK
# ═════════════════════════════════════════════════════════════

class RoutingFeedbackCreate(BaseModel):
    routing_log_id:    uuid.UUID
    feedback_type:     str  = Field(..., pattern=r"^(wrong_provider|wrong_model|unnecessary_fallback|too_expensive|quality_poor|too_slow|correct)$")
    rating:            Optional[int]  = Field(None, ge=1, le=5)
    suggested_provider:Optional[str] = None
    suggested_model:   Optional[str] = None
    notes:             Optional[str] = None


class RoutingFeedbackOut(BaseModel):
    id:               uuid.UUID
    routing_log_id:   uuid.UUID
    organization_id:  Optional[uuid.UUID]
    submitted_by:     Optional[uuid.UUID]
    feedback_type:    str
    rating:           Optional[int]
    suggested_provider:Optional[str]
    suggested_model:  Optional[str]
    notes:            Optional[str]
    is_reviewed:      bool
    reviewed_by:      Optional[uuid.UUID]
    created_at:       datetime
    reviewed_at:      Optional[datetime]

    class Config:
        from_attributes = True


# ═════════════════════════════════════════════════════════════
# ANALYTICS / DASHBOARD
# ═════════════════════════════════════════════════════════════

class ProviderUsageStat(BaseModel):
    provider:       str
    request_count:  int
    total_tokens:   int
    total_cost_usd: float
    avg_latency_ms: Optional[float]
    fallback_count: int
    fallback_pct:   float    # 0.0 – 100.0


class RoutingAnalyticsSummary(BaseModel):
    period_start:    datetime
    period_end:      datetime
    total_requests:  int
    total_tokens:    int
    total_cost_usd:  float
    avg_cost_usd:    float
    avg_latency_ms:  Optional[float]
    fallback_count:  int
    fallback_pct:    float
    providers:       List[ProviderUsageStat]
    top_categories:  List[dict]         # [{slug, count}]
    error_count:     int
    error_pct:       float


class DashboardResponse(BaseModel):
    summary:         RoutingAnalyticsSummary
    recent_fallbacks:List[RoutingLogOut]
    pending_feedback:int
    active_rules:    int
