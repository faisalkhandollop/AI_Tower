"""
app/api/routing_governance.py
==============================
Phase 3 Routing Governance API.

Routes:
    # Routing Rules
    GET    /routing/rules              → list rules (paginated)
    POST   /routing/rules              → create rule
    GET    /routing/rules/{id}         → get rule
    PATCH  /routing/rules/{id}         → update rule
    DELETE /routing/rules/{id}         → soft-delete (is_active = False)

    # Routing Logs
    GET    /routing/logs               → list logs (paginated, filterable)
    GET    /routing/logs/{id}          → get single log

    # Routing Feedback
    POST   /routing/feedback           → submit feedback on a routing decision
    GET    /routing/feedback           → list feedback (admin)
    PATCH  /routing/feedback/{id}/review → mark feedback as reviewed

    # Prompt Categories
    GET    /routing/categories         → list categories
    POST   /routing/categories         → create category
    PATCH  /routing/categories/{id}    → update category

    # Analytics
    GET    /routing/analytics          → aggregated dashboard stats
    GET    /routing/dashboard          → full dashboard payload
"""

import uuid
import logging
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models_routing import (
    PromptCategory,
    RoutingRule,
    RoutingLog,
    RoutingFeedback,
)
from app.schemas.routing_governance import (
    PromptCategoryCreate,
    PromptCategoryUpdate,
    PromptCategoryOut,
    RoutingRuleCreate,
    RoutingRuleUpdate,
    RoutingRuleOut,
    RoutingLogOut,
    RoutingLogListResponse,
    RoutingFeedbackCreate,
    RoutingFeedbackOut,
    DashboardResponse,
)
from app.services.routing_governance import get_routing_analytics
from app.services.auth import get_current_user
from app.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing", tags=["Routing Governance"])


# ═════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════

def _require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in ("admin", "super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin required.")
    return current_user


def _get_rule_or_404(rule_id: uuid.UUID, db: Session) -> RoutingRule:
    rule = db.query(RoutingRule).filter(RoutingRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Routing rule not found.")
    return rule


# ═════════════════════════════════════════════════════════════
# PROMPT CATEGORIES
# ═════════════════════════════════════════════════════════════

@router.get(
    "/categories",
    response_model=List[PromptCategoryOut],
    summary="List prompt categories",
)
def list_categories(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = db.query(PromptCategory)
    if not include_inactive:
        q = q.filter(PromptCategory.is_active == True)
    return q.order_by(PromptCategory.name).all()


@router.post(
    "/categories",
    response_model=PromptCategoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create prompt category (admin)",
)
def create_category(
    body: PromptCategoryCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    if db.query(PromptCategory).filter_by(slug=body.slug).first():
        raise HTTPException(status_code=409, detail=f"Category slug '{body.slug}' already exists.")
    cat = PromptCategory(id=uuid.uuid4(), **body.model_dump())
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return cat


@router.patch(
    "/categories/{category_id}",
    response_model=PromptCategoryOut,
    summary="Update prompt category (admin)",
)
def update_category(
    category_id: uuid.UUID,
    body: PromptCategoryUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    cat = db.query(PromptCategory).filter_by(id=category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found.")
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(cat, field, value)
    db.commit()
    db.refresh(cat)
    return cat


# ═════════════════════════════════════════════════════════════
# ROUTING RULES
# ═════════════════════════════════════════════════════════════

@router.get(
    "/rules",
    response_model=List[RoutingRuleOut],
    summary="List routing rules",
)
def list_rules(
    organization_id: Optional[uuid.UUID] = None,
    is_active:       Optional[bool]      = None,
    action:          Optional[str]       = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    q = db.query(RoutingRule)
    if organization_id is not None:
        q = q.filter(RoutingRule.organization_id == organization_id)
    if is_active is not None:
        q = q.filter(RoutingRule.is_active == is_active)
    if action:
        q = q.filter(RoutingRule.action == action)
    return q.order_by(RoutingRule.priority.asc()).offset(skip).limit(limit).all()


@router.post(
    "/rules",
    response_model=RoutingRuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create routing rule (admin)",
)
def create_rule(
    body: RoutingRuleCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    data = body.model_dump()
    data["id"]         = uuid.uuid4()
    data["created_by"] = admin.id
    data["updated_by"] = admin.id
    rule = RoutingRule(**data)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    logger.info("Routing rule '%s' created by %s.", rule.name, admin.email)
    return rule


@router.get(
    "/rules/{rule_id}",
    response_model=RoutingRuleOut,
    summary="Get routing rule",
)
def get_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    return _get_rule_or_404(rule_id, db)


@router.patch(
    "/rules/{rule_id}",
    response_model=RoutingRuleOut,
    summary="Update routing rule (admin)",
)
def update_rule(
    rule_id: uuid.UUID,
    body: RoutingRuleUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    rule = _get_rule_or_404(rule_id, db)
    updates = body.model_dump(exclude_none=True)
    updates["updated_by"] = admin.id
    for field, value in updates.items():
        setattr(rule, field, value)
    db.commit()
    db.refresh(rule)
    logger.info("Routing rule '%s' updated by %s.", rule.name, admin.email)
    return rule


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable routing rule (soft delete)",
)
def delete_rule(
    rule_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    rule = _get_rule_or_404(rule_id, db)
    rule.is_active = False
    rule.updated_by = admin.id
    db.commit()
    logger.info("Routing rule '%s' deactivated by %s.", rule.name, admin.email)


# ═════════════════════════════════════════════════════════════
# ROUTING LOGS
# ═════════════════════════════════════════════════════════════

@router.get(
    "/logs",
    response_model=RoutingLogListResponse,
    summary="List routing logs (paginated)",
)
def list_routing_logs(
    organization_id:  Optional[uuid.UUID] = None,
    user_id:          Optional[uuid.UUID] = None,
    provider:         Optional[str]       = None,
    was_fallback:     Optional[bool]      = None,
    category_slug:    Optional[str]       = None,
    date_from:        Optional[datetime]  = None,
    date_to:          Optional[datetime]  = None,
    page:  int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    q = db.query(RoutingLog)
    if organization_id:
        q = q.filter(RoutingLog.organization_id == organization_id)
    if user_id:
        q = q.filter(RoutingLog.user_id == user_id)
    if provider:
        q = q.filter(RoutingLog.selected_provider == provider)
    if was_fallback is not None:
        q = q.filter(RoutingLog.was_fallback == was_fallback)
    if category_slug:
        q = q.filter(RoutingLog.category_slug == category_slug)
    if date_from:
        q = q.filter(RoutingLog.created_at >= date_from)
    if date_to:
        q = q.filter(RoutingLog.created_at <= date_to)

    total  = q.count()
    offset = (page - 1) * limit
    items  = q.order_by(RoutingLog.created_at.desc()).offset(offset).limit(limit).all()

    return RoutingLogListResponse(total=total, page=page, limit=limit, items=items)


@router.get(
    "/logs/{log_id}",
    response_model=RoutingLogOut,
    summary="Get single routing log",
)
def get_routing_log(
    log_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    log = db.query(RoutingLog).filter_by(id=log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Routing log not found.")
    return log


# ═════════════════════════════════════════════════════════════
# ROUTING FEEDBACK
# ═════════════════════════════════════════════════════════════

@router.post(
    "/feedback",
    response_model=RoutingFeedbackOut,
    status_code=status.HTTP_201_CREATED,
    summary="Submit feedback on a routing decision",
)
def submit_feedback(
    body: RoutingFeedbackCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Validate referenced log exists
    log = db.query(RoutingLog).filter_by(id=body.routing_log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Routing log not found.")

    fb = RoutingFeedback(
        id=uuid.uuid4(),
        organization_id=log.organization_id,
        submitted_by=current_user.id,
        **body.model_dump(),
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb


@router.get(
    "/feedback",
    response_model=List[RoutingFeedbackOut],
    summary="List routing feedback (admin)",
)
def list_feedback(
    organization_id: Optional[uuid.UUID] = None,
    feedback_type:   Optional[str]       = None,
    is_reviewed:     Optional[bool]      = None,
    skip:  int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    q = db.query(RoutingFeedback)
    if organization_id:
        q = q.filter(RoutingFeedback.organization_id == organization_id)
    if feedback_type:
        q = q.filter(RoutingFeedback.feedback_type == feedback_type)
    if is_reviewed is not None:
        q = q.filter(RoutingFeedback.is_reviewed == is_reviewed)
    return q.order_by(RoutingFeedback.created_at.desc()).offset(skip).limit(limit).all()


@router.patch(
    "/feedback/{feedback_id}/review",
    response_model=RoutingFeedbackOut,
    summary="Mark feedback as reviewed (admin)",
)
def review_feedback(
    feedback_id: uuid.UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    fb = db.query(RoutingFeedback).filter_by(id=feedback_id).first()
    if not fb:
        raise HTTPException(status_code=404, detail="Feedback not found.")
    fb.is_reviewed  = True
    fb.reviewed_by  = admin.id
    fb.reviewed_at  = datetime.now(timezone.utc)
    db.commit()
    db.refresh(fb)
    return fb


# ═════════════════════════════════════════════════════════════
# ANALYTICS
# ═════════════════════════════════════════════════════════════

@router.get(
    "/analytics",
    summary="Routing analytics summary",
)
def routing_analytics(
    organization_id: Optional[uuid.UUID] = None,
    days: int = Query(30, ge=1, le=365, description="Lookback window in days"),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    return get_routing_analytics(db, organization_id=organization_id, days=days)


@router.get(
    "/dashboard",
    summary="Full routing governance dashboard",
)
def routing_dashboard(
    organization_id: Optional[uuid.UUID] = None,
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
    admin: User = Depends(_require_admin),
):
    summary = get_routing_analytics(db, organization_id=organization_id, days=days)

    # Recent fallbacks (last 10)
    fb_q = db.query(RoutingLog).filter(RoutingLog.was_fallback == True)
    if organization_id:
        fb_q = fb_q.filter(RoutingLog.organization_id == organization_id)
    recent_fallbacks = fb_q.order_by(RoutingLog.created_at.desc()).limit(10).all()

    # Pending feedback count
    pending_fb_q = db.query(RoutingFeedback).filter(RoutingFeedback.is_reviewed == False)
    if organization_id:
        pending_fb_q = pending_fb_q.filter(RoutingFeedback.organization_id == organization_id)
    pending_feedback = pending_fb_q.count()

    # Active rules count
    rules_q = db.query(RoutingRule).filter(RoutingRule.is_active == True)
    if organization_id:
        rules_q = rules_q.filter(
            (RoutingRule.organization_id == organization_id)
            | (RoutingRule.organization_id == None)
        )
    active_rules = rules_q.count()

    return {
        "summary":          summary,
        "recent_fallbacks": recent_fallbacks,
        "pending_feedback": pending_feedback,
        "active_rules":     active_rules,
    }
