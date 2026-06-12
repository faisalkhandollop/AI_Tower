"""
api/subscriptions.py — Subscription Management
================================================
Super Admin manages plans and assigns/upgrades/suspends subscriptions.

Routes:
    # ── Plans (Super Admin only) ──────────────────────────────────────────────
    POST   /subscriptions/plans                    → Create plan
    GET    /subscriptions/plans                    → List all plans
    GET    /subscriptions/plans/{plan_id}          → Get plan detail
    PUT    /subscriptions/plans/{plan_id}          → Update plan
    DELETE /subscriptions/plans/{plan_id}          → Deactivate plan

    # ── Subscriptions (Super Admin only) ─────────────────────────────────────
    POST   /subscriptions/                         → Assign subscription to org
    GET    /subscriptions/                         → List all subscriptions (with filters)
    GET    /subscriptions/dashboard                → Super Admin dashboard summary
    GET    /subscriptions/{sub_id}                 → Get subscription detail
    PUT    /subscriptions/{sub_id}                 → Update subscription (change plan, quotas)
    POST   /subscriptions/{sub_id}/upgrade         → Upgrade to higher tier plan
    POST   /subscriptions/{sub_id}/downgrade       → Downgrade to lower tier plan
    POST   /subscriptions/{sub_id}/suspend         → Suspend subscription
    POST   /subscriptions/{sub_id}/unsuspend       → Reactivate suspended subscription
    POST   /subscriptions/{sub_id}/renew           → Renew (extend period_end by 30 days)
    POST   /subscriptions/{sub_id}/cancel          → Cancel subscription
    DELETE /subscriptions/{sub_id}                 → Hard delete (Super Admin only)

    # ── Org self-service (Org Admin reads their own) ──────────────────────────
    GET    /subscriptions/my                       → Current org's subscription + plan
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import Organization, User
from app.db.models_enterprise import (
    SubscriptionPlan,
    Subscription,
    PLAN_TIERS,
    SUBSCRIPTION_STATUSES,
)
from app.services.auth import get_current_user
from app.services.rbac import require_super_admin, require_org_admin

router = APIRouter(prefix="/subscriptions", tags=["Subscription Management"])

TIER_ORDER = {"free": 0, "pro": 1, "enterprise": 2}


# =============================================================================
# Pydantic Schemas
# =============================================================================

class CreatePlanRequest(BaseModel):
    name:                  str            = Field(..., min_length=2, max_length=100)
    tier:                  str            = Field(..., description="free | pro | enterprise")
    description:           Optional[str]  = None
    price_monthly_usd:     float          = Field(0.0, ge=0)
    price_yearly_usd:      Optional[float]= None
    max_users:             Optional[int]  = Field(None, ge=1)
    max_teams:             Optional[int]  = Field(None, ge=1)
    max_departments:       Optional[int]  = Field(None, ge=1)
    monthly_token_quota:   Optional[int]  = Field(None, ge=1)
    monthly_request_quota: Optional[int]  = Field(None, ge=1)
    max_api_keys:          Optional[int]  = Field(None, ge=1)
    allowed_providers:     List[str]      = Field(default_factory=list)
    features:              dict           = Field(default_factory=dict)
    is_public:             bool           = True


class UpdatePlanRequest(BaseModel):
    name:                  Optional[str]   = Field(None, min_length=2, max_length=100)
    description:           Optional[str]   = None
    price_monthly_usd:     Optional[float] = Field(None, ge=0)
    price_yearly_usd:      Optional[float] = None
    max_users:             Optional[int]   = None
    max_teams:             Optional[int]   = None
    max_departments:       Optional[int]   = None
    monthly_token_quota:   Optional[int]   = None
    monthly_request_quota: Optional[int]   = None
    max_api_keys:          Optional[int]   = None
    allowed_providers:     Optional[List[str]] = None
    features:              Optional[dict]  = None
    is_active:             Optional[bool]  = None
    is_public:             Optional[bool]  = None


class AssignSubscriptionRequest(BaseModel):
    organization_id: str
    plan_id:         str
    status:          str              = Field("active", description="active | trialing")
    period_days:     int              = Field(30, ge=1, le=730, description="Billing period length in days")
    trial_days:      Optional[int]    = Field(None, ge=1, le=90)
    token_quota_override:    Optional[int] = None
    request_quota_override:  Optional[int] = None
    user_limit_override:     Optional[int] = None
    notes:           Optional[str]    = None


class UpdateSubscriptionRequest(BaseModel):
    plan_id:                 Optional[str]  = None
    status:                  Optional[str]  = None
    token_quota_override:    Optional[int]  = None
    request_quota_override:  Optional[int]  = None
    user_limit_override:     Optional[int]  = None
    notes:                   Optional[str]  = None


class SuspendRequest(BaseModel):
    reason: str = Field(..., min_length=5, max_length=500)


class RenewRequest(BaseModel):
    period_days: int = Field(30, ge=1, le=730)


class ChangePlanRequest(BaseModel):
    plan_id: str
    notes:   Optional[str] = None


# =============================================================================
# Serializers
# =============================================================================

def _plan_dict(p: SubscriptionPlan) -> dict:
    return {
        "id":                    str(p.id),
        "name":                  p.name,
        "tier":                  p.tier,
        "description":           p.description,
        "price_monthly_usd":     float(p.price_monthly_usd),
        "price_yearly_usd":      float(p.price_yearly_usd) if p.price_yearly_usd else None,
        "max_users":             p.max_users,
        "max_teams":             p.max_teams,
        "max_departments":       p.max_departments,
        "monthly_token_quota":   p.monthly_token_quota,
        "monthly_request_quota": p.monthly_request_quota,
        "max_api_keys":          p.max_api_keys,
        "allowed_providers":     p.allowed_providers or [],
        "features":              p.features or {},
        "is_active":             p.is_active,
        "is_public":             p.is_public,
        "created_at":            p.created_at.isoformat() if p.created_at else None,
        "updated_at":            p.updated_at.isoformat() if p.updated_at else None,
    }


def _sub_dict(s: Subscription, db: Session) -> dict:
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == s.plan_id).first()
    org  = db.query(Organization).filter(Organization.id == s.organization_id).first()
    return {
        "id":                     str(s.id),
        "organization_id":        str(s.organization_id),
        "organization_name":      org.name if org else None,
        "plan_id":                str(s.plan_id),
        "plan_name":              plan.name if plan else None,
        "plan_tier":              plan.tier if plan else None,
        "status":                 s.status,
        "current_period_start":   s.current_period_start.isoformat() if s.current_period_start else None,
        "current_period_end":     s.current_period_end.isoformat() if s.current_period_end else None,
        "token_quota":            s.token_quota_override or (plan.monthly_token_quota if plan else None),
        "request_quota":          s.request_quota_override or (plan.monthly_request_quota if plan else None),
        "user_limit":             s.user_limit_override or (plan.max_users if plan else None),
        "token_quota_override":   s.token_quota_override,
        "request_quota_override": s.request_quota_override,
        "user_limit_override":    s.user_limit_override,
        "trial_ends_at":          s.trial_ends_at.isoformat() if s.trial_ends_at else None,
        "cancelled_at":           s.cancelled_at.isoformat() if s.cancelled_at else None,
        "suspended_at":           s.suspended_at.isoformat() if s.suspended_at else None,
        "suspension_reason":      s.suspension_reason,
        "notes":                  s.notes,
        "created_at":             s.created_at.isoformat() if s.created_at else None,
        "updated_at":             s.updated_at.isoformat() if s.updated_at else None,
    }


# =============================================================================
# Helpers
# =============================================================================

def _get_plan(plan_id: str, db: Session) -> SubscriptionPlan:
    p = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not p:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found")
    return p


def _get_sub(sub_id: str, db: Session) -> Subscription:
    s = db.query(Subscription).filter(Subscription.id == sub_id).first()
    if not s:
        raise HTTPException(status_code=404, detail=f"Subscription '{sub_id}' not found")
    return s


def _validate_tier(tier: str) -> str:
    if tier not in TIER_ORDER:
        raise HTTPException(status_code=400, detail=f"Invalid tier '{tier}'. Must be: free, pro, enterprise")
    return tier


def _validate_status(s: str) -> str:
    if s not in SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status '{s}'")
    return s


# =============================================================================
# PLAN ENDPOINTS
# =============================================================================

@router.post(
    "/plans",
    status_code=201,
    summary="Create a subscription plan (Super Admin only)",
)
def create_plan(
    payload: CreatePlanRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    _validate_tier(payload.tier)

    # Check name uniqueness
    existing = db.query(SubscriptionPlan).filter(SubscriptionPlan.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Plan named '{payload.name}' already exists")

    plan = SubscriptionPlan(
        id=uuid.uuid4(),
        name=payload.name,
        tier=payload.tier,
        description=payload.description,
        price_monthly_usd=payload.price_monthly_usd,
        price_yearly_usd=payload.price_yearly_usd,
        max_users=payload.max_users,
        max_teams=payload.max_teams,
        max_departments=payload.max_departments,
        monthly_token_quota=payload.monthly_token_quota,
        monthly_request_quota=payload.monthly_request_quota,
        max_api_keys=payload.max_api_keys,
        allowed_providers=payload.allowed_providers,
        features=payload.features,
        is_active=True,
        is_public=payload.is_public,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _plan_dict(plan)


@router.get(
    "/plans",
    summary="List subscription plans",
)
def list_plans(
    tier:       Optional[str]  = Query(None, description="Filter by tier: free|pro|enterprise"),
    is_active:  Optional[bool] = Query(None),
    is_public:  Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(SubscriptionPlan)
    if tier:
        q = q.filter(SubscriptionPlan.tier == tier)
    if is_active is not None:
        q = q.filter(SubscriptionPlan.is_active == is_active)
    if is_public is not None:
        q = q.filter(SubscriptionPlan.is_public == is_public)
    plans = q.order_by(SubscriptionPlan.price_monthly_usd.asc()).all()
    return {"plans": [_plan_dict(p) for p in plans], "total": len(plans)}


@router.get(
    "/plans/{plan_id}",
    summary="Get a single plan",
)
def get_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _plan_dict(_get_plan(plan_id, db))


@router.put(
    "/plans/{plan_id}",
    summary="Update a subscription plan (Super Admin only)",
)
def update_plan(
    plan_id: str,
    payload: UpdatePlanRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    plan = _get_plan(plan_id, db)

    if payload.name is not None:
        dupe = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.name == payload.name,
            SubscriptionPlan.id != plan.id,
        ).first()
        if dupe:
            raise HTTPException(409, f"Plan named '{payload.name}' already exists")
        plan.name = payload.name

    if payload.description is not None:        plan.description           = payload.description
    if payload.price_monthly_usd is not None:  plan.price_monthly_usd    = payload.price_monthly_usd
    if payload.price_yearly_usd is not None:   plan.price_yearly_usd     = payload.price_yearly_usd
    if payload.max_users is not None:          plan.max_users             = payload.max_users
    if payload.max_teams is not None:          plan.max_teams             = payload.max_teams
    if payload.max_departments is not None:    plan.max_departments       = payload.max_departments
    if payload.monthly_token_quota is not None:   plan.monthly_token_quota   = payload.monthly_token_quota
    if payload.monthly_request_quota is not None: plan.monthly_request_quota = payload.monthly_request_quota
    if payload.max_api_keys is not None:       plan.max_api_keys          = payload.max_api_keys
    if payload.allowed_providers is not None:  plan.allowed_providers     = payload.allowed_providers
    if payload.features is not None:           plan.features              = payload.features
    if payload.is_active is not None:          plan.is_active             = payload.is_active
    if payload.is_public is not None:          plan.is_public             = payload.is_public

    db.commit()
    db.refresh(plan)
    return _plan_dict(plan)


@router.delete(
    "/plans/{plan_id}",
    summary="Deactivate a plan (Super Admin only). Does not delete existing subscriptions.",
)
def deactivate_plan(
    plan_id: str,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    plan = _get_plan(plan_id, db)
    plan.is_active = False
    plan.is_public = False
    db.commit()
    return {"message": f"Plan '{plan.name}' deactivated", "plan_id": plan_id}


# =============================================================================
# SUBSCRIPTION ENDPOINTS
# =============================================================================

@router.post(
    "/",
    status_code=201,
    summary="Assign a subscription to an organization (Super Admin only)",
)
def assign_subscription(
    payload: AssignSubscriptionRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    # Validate org exists
    org = db.query(Organization).filter(Organization.id == payload.organization_id).first()
    if not org:
        raise HTTPException(404, f"Organization '{payload.organization_id}' not found")

    # Validate plan exists and is active
    plan = _get_plan(payload.plan_id, db)
    if not plan.is_active:
        raise HTTPException(400, f"Plan '{plan.name}' is not active")

    # Only one subscription per org
    existing = db.query(Subscription).filter(
        Subscription.organization_id == payload.organization_id
    ).first()
    if existing:
        raise HTTPException(
            409,
            f"Organization already has a subscription (id: {existing.id}). "
            "Use PUT /subscriptions/{id} to update it."
        )

    _validate_status(payload.status)
    now = datetime.now(timezone.utc)
    period_end = now + timedelta(days=payload.period_days)
    trial_ends_at = now + timedelta(days=payload.trial_days) if payload.trial_days else None

    actor_id = _sa.get("sub")
    sub = Subscription(
        id=uuid.uuid4(),
        organization_id=payload.organization_id,
        plan_id=payload.plan_id,
        status=payload.status,
        current_period_start=now,
        current_period_end=period_end,
        trial_ends_at=trial_ends_at,
        token_quota_override=payload.token_quota_override,
        request_quota_override=payload.request_quota_override,
        user_limit_override=payload.user_limit_override,
        assigned_by=actor_id,
        notes=payload.notes,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return _sub_dict(sub, db)


@router.get(
    "/dashboard",
    summary="Subscription dashboard — plan counts and status breakdown (Super Admin only)",
)
def subscription_dashboard(
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    total_orgs = db.query(func.count(Organization.id)).scalar() or 0
    total_subs = db.query(func.count(Subscription.id)).scalar() or 0

    # Subscriptions by status
    status_rows = (
        db.query(Subscription.status, func.count(Subscription.id).label("count"))
        .group_by(Subscription.status)
        .all()
    )
    by_status = {row.status: row.count for row in status_rows}

    # Subscriptions by plan tier
    tier_rows = (
        db.query(SubscriptionPlan.tier, func.count(Subscription.id).label("count"))
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .group_by(SubscriptionPlan.tier)
        .all()
    )
    by_tier = {row.tier: row.count for row in tier_rows}

    # Orgs without any subscription
    orgs_with_sub_ids = db.query(Subscription.organization_id).subquery()
    orgs_without_sub = (
        db.query(func.count(Organization.id))
        .filter(~Organization.id.in_(orgs_with_sub_ids))
        .scalar() or 0
    )

    # Expiring soon (within 7 days, still active)
    soon = datetime.now(timezone.utc) + timedelta(days=7)
    expiring_soon = (
        db.query(func.count(Subscription.id))
        .filter(
            Subscription.status == "active",
            Subscription.current_period_end <= soon,
        )
        .scalar() or 0
    )

    # Plan summary
    plans = db.query(SubscriptionPlan).filter(SubscriptionPlan.is_active == True).all()

    return {
        "total_organizations":     total_orgs,
        "total_subscriptions":     total_subs,
        "organizations_without_subscription": orgs_without_sub,
        "expiring_within_7_days":  expiring_soon,
        "by_status": {
            "active":    by_status.get("active",    0),
            "trialing":  by_status.get("trialing",  0),
            "past_due":  by_status.get("past_due",  0),
            "suspended": by_status.get("suspended", 0),
            "cancelled": by_status.get("cancelled", 0),
            "expired":   by_status.get("expired",   0),
        },
        "by_tier": {
            "free":       by_tier.get("free",       0),
            "pro":        by_tier.get("pro",        0),
            "enterprise": by_tier.get("enterprise", 0),
        },
        "active_plans": [_plan_dict(p) for p in plans],
    }


@router.get(
    "/",
    summary="List all subscriptions (Super Admin only)",
)
def list_subscriptions(
    status:  Optional[str] = Query(None, description="Filter by status"),
    tier:    Optional[str] = Query(None, description="Filter by plan tier"),
    org_id:  Optional[str] = Query(None, description="Filter by org UUID"),
    limit:   int = Query(50, ge=1, le=200),
    offset:  int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    q = db.query(Subscription)

    if status:
        _validate_status(status)
        q = q.filter(Subscription.status == status)
    if org_id:
        q = q.filter(Subscription.organization_id == org_id)
    if tier:
        _validate_tier(tier)
        plan_ids = db.query(SubscriptionPlan.id).filter(SubscriptionPlan.tier == tier).subquery()
        q = q.filter(Subscription.plan_id.in_(plan_ids))

    total = q.count()
    subs  = q.order_by(Subscription.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "subscriptions": [_sub_dict(s, db) for s in subs],
        "total":  total,
        "limit":  limit,
        "offset": offset,
    }


@router.get(
    "/my",
    summary="Get current organization's subscription (Org Admin)",
)
def get_my_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _oa: dict = Depends(require_org_admin),
):
    # Find user's org via UserTeam or direct org lookup
    # For now, look up by user's org_id from JWT or first matching org
    from app.db.models import UserTeam, Team
    team_row = (
        db.query(UserTeam)
        .filter(UserTeam.user_id == current_user.id)
        .first()
    )
    if not team_row:
        raise HTTPException(404, "No organization found for your account")

    team = db.query(Team).filter(Team.id == team_row.team_id).first()
    if not team:
        raise HTTPException(404, "Team not found")

    sub = db.query(Subscription).filter(
        Subscription.organization_id == team.org_id
    ).first()
    if not sub:
        raise HTTPException(404, "Your organization does not have an active subscription")

    return _sub_dict(sub, db)


@router.get(
    "/{sub_id}",
    summary="Get subscription detail (Super Admin only)",
)
def get_subscription(
    sub_id: str,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    return _sub_dict(_get_sub(sub_id, db), db)


@router.put(
    "/{sub_id}",
    summary="Update subscription — change plan, quotas, notes (Super Admin only)",
)
def update_subscription(
    sub_id: str,
    payload: UpdateSubscriptionRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub = _get_sub(sub_id, db)

    if payload.plan_id is not None:
        plan = _get_plan(payload.plan_id, db)
        if not plan.is_active:
            raise HTTPException(400, f"Plan '{plan.name}' is not active")
        sub.plan_id = plan.id

    if payload.status is not None:
        _validate_status(payload.status)
        sub.status = payload.status

    if payload.token_quota_override is not None:   sub.token_quota_override   = payload.token_quota_override
    if payload.request_quota_override is not None: sub.request_quota_override = payload.request_quota_override
    if payload.user_limit_override is not None:    sub.user_limit_override    = payload.user_limit_override
    if payload.notes is not None:                  sub.notes                  = payload.notes

    sub.assigned_by = _sa.get("sub")
    db.commit()
    db.refresh(sub)
    return _sub_dict(sub, db)


@router.post(
    "/{sub_id}/upgrade",
    summary="Upgrade to a higher-tier plan (Super Admin only)",
)
def upgrade_subscription(
    sub_id: str,
    payload: ChangePlanRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub        = _get_sub(sub_id, db)
    new_plan   = _get_plan(payload.plan_id, db)
    old_plan   = _get_plan(str(sub.plan_id), db)

    if TIER_ORDER.get(new_plan.tier, 0) <= TIER_ORDER.get(old_plan.tier, 0):
        raise HTTPException(
            400,
            f"Upgrade requires a higher tier. Current: '{old_plan.tier}', Requested: '{new_plan.tier}'"
        )
    if not new_plan.is_active:
        raise HTTPException(400, f"Plan '{new_plan.name}' is not active")

    sub.plan_id     = new_plan.id
    sub.status      = "active"
    sub.assigned_by = _sa.get("sub")
    if payload.notes:
        sub.notes = payload.notes

    db.commit()
    db.refresh(sub)
    return {
        "message":  f"Upgraded from '{old_plan.name}' to '{new_plan.name}'",
        "subscription": _sub_dict(sub, db),
    }


@router.post(
    "/{sub_id}/downgrade",
    summary="Downgrade to a lower-tier plan (Super Admin only)",
)
def downgrade_subscription(
    sub_id: str,
    payload: ChangePlanRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub      = _get_sub(sub_id, db)
    new_plan = _get_plan(payload.plan_id, db)
    old_plan = _get_plan(str(sub.plan_id), db)

    if TIER_ORDER.get(new_plan.tier, 0) >= TIER_ORDER.get(old_plan.tier, 0):
        raise HTTPException(
            400,
            f"Downgrade requires a lower tier. Current: '{old_plan.tier}', Requested: '{new_plan.tier}'"
        )
    if not new_plan.is_active:
        raise HTTPException(400, f"Plan '{new_plan.name}' is not active")

    sub.plan_id     = new_plan.id
    sub.assigned_by = _sa.get("sub")
    if payload.notes:
        sub.notes = payload.notes

    # Clear overrides that may exceed the new plan's limits
    plan_max_tokens = new_plan.monthly_token_quota
    if sub.token_quota_override and plan_max_tokens and sub.token_quota_override > plan_max_tokens:
        sub.token_quota_override = None

    db.commit()
    db.refresh(sub)
    return {
        "message":      f"Downgraded from '{old_plan.name}' to '{new_plan.name}'",
        "subscription": _sub_dict(sub, db),
    }


@router.post(
    "/{sub_id}/suspend",
    summary="Suspend subscription — org loses AI access (Super Admin only)",
)
def suspend_subscription(
    sub_id: str,
    payload: SuspendRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub = _get_sub(sub_id, db)

    if sub.status == "suspended":
        raise HTTPException(400, "Subscription is already suspended")

    sub.status           = "suspended"
    sub.suspended_at     = datetime.now(timezone.utc)
    sub.suspension_reason = payload.reason
    sub.assigned_by      = _sa.get("sub")

    db.commit()
    db.refresh(sub)
    return {
        "message":      "Subscription suspended",
        "subscription": _sub_dict(sub, db),
    }


@router.post(
    "/{sub_id}/unsuspend",
    summary="Reactivate a suspended subscription (Super Admin only)",
)
def unsuspend_subscription(
    sub_id: str,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub = _get_sub(sub_id, db)

    if sub.status != "suspended":
        raise HTTPException(400, f"Subscription is not suspended (current status: '{sub.status}')")

    sub.status            = "active"
    sub.suspended_at      = None
    sub.suspension_reason = None
    sub.assigned_by       = _sa.get("sub")

    db.commit()
    db.refresh(sub)
    return {
        "message":      "Subscription reactivated",
        "subscription": _sub_dict(sub, db),
    }


@router.post(
    "/{sub_id}/renew",
    summary="Renew subscription — extends billing period (Super Admin only)",
)
def renew_subscription(
    sub_id: str,
    payload: RenewRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub = _get_sub(sub_id, db)

    if sub.status == "cancelled":
        raise HTTPException(400, "Cannot renew a cancelled subscription. Assign a new one instead.")

    # Extend from period_end (or now if already expired)
    base = sub.current_period_end or datetime.now(timezone.utc)
    if base < datetime.now(timezone.utc):
        base = datetime.now(timezone.utc)

    sub.current_period_start = base
    sub.current_period_end   = base + timedelta(days=payload.period_days)
    sub.status               = "active"
    sub.assigned_by          = _sa.get("sub")

    db.commit()
    db.refresh(sub)
    return {
        "message":      f"Subscription renewed for {payload.period_days} days",
        "new_period_end": sub.current_period_end.isoformat(),
        "subscription": _sub_dict(sub, db),
    }


@router.post(
    "/{sub_id}/cancel",
    summary="Cancel subscription (Super Admin only). Org retains access until period_end.",
)
def cancel_subscription(
    sub_id: str,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub = _get_sub(sub_id, db)

    if sub.status == "cancelled":
        raise HTTPException(400, "Subscription is already cancelled")

    sub.status       = "cancelled"
    sub.cancelled_at = datetime.now(timezone.utc)
    sub.assigned_by  = _sa.get("sub")

    db.commit()
    db.refresh(sub)
    return {
        "message":      "Subscription cancelled. Access continues until period end.",
        "access_until": sub.current_period_end.isoformat() if sub.current_period_end else None,
        "subscription": _sub_dict(sub, db),
    }


@router.delete(
    "/{sub_id}",
    status_code=204,
    summary="Hard delete subscription record (Super Admin only)",
)
def delete_subscription(
    sub_id: str,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    sub = _get_sub(sub_id, db)
    db.delete(sub)
    db.commit()