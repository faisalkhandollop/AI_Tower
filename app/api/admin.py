from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import UsageLog, User
from app.schemas.admin import (
    DashboardResponse,
    UserReportResponse,
    UserUsageStat,
    ModelReportResponse,
    ProviderStat,
    ModelStat,
)
from app.services.auth import require_admin

router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
)

HEAVY_USAGE_THRESHOLD = 10_000


# ── GET /admin/dashboard ───────────────────────────────────────────────────────
@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="Platform-wide usage summary (admin only)",
)
def get_dashboard(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    result = db.query(
        func.count(UsageLog.id)                              .label("total_requests"),
        func.coalesce(func.sum(UsageLog.input_tokens),  0)  .label("total_input_tokens"),
        func.coalesce(func.sum(UsageLog.output_tokens), 0)  .label("total_output_tokens"),
        func.coalesce(func.sum(UsageLog.total_tokens),  0)  .label("total_tokens"),
        func.coalesce(func.sum(UsageLog.cost),          0)  .label("total_cost"),
    ).one()

    total_requests = result.total_requests
    total_cost     = round(float(result.total_cost), 6)
    avg_cost       = round(total_cost / total_requests, 6) if total_requests > 0 else 0.0

    return {
        "total_requests":       total_requests,
        "total_input_tokens":   int(result.total_input_tokens),
        "total_output_tokens":  int(result.total_output_tokens),
        "total_tokens":         int(result.total_tokens),
        "total_cost":           total_cost,
        "avg_cost_per_request": avg_cost,
    }


# ── GET /admin/users ───────────────────────────────────────────────────────────
@router.get(
    "/users",
    response_model=UserReportResponse,
    summary="Per-user usage and quota report (admin only)",
)
def get_user_report(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    LEFT JOIN ensures users with zero usage still appear in the report.
    Quota remaining = user.token_quota - SUM(total_tokens).
    """
    rows = (
        db.query(
            User.id         .label("user_id"),
            User.name,
            User.email,
            User.department,
            User.role,
            User.token_quota,
            User.is_active,
            func.count(UsageLog.id)                            .label("total_requests"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0)  .label("total_tokens"),
            func.coalesce(func.sum(UsageLog.cost),         0)  .label("total_cost"),
        )
        .outerjoin(UsageLog, UsageLog.user_id == User.id)   # LEFT JOIN — include zero-usage users
        .group_by(
            User.id,
            User.name,
            User.email,
            User.department,
            User.role,
            User.token_quota,
            User.is_active,
        )
        .all()
    )

    def _build(row) -> UserUsageStat:
        used       = int(row.total_tokens)
        limit      = int(row.token_quota)
        remaining  = max(limit - used, 0)
        return UserUsageStat(
            user_id=         str(row.user_id),
            name=            row.name,
            email=           row.email,
            department=      row.department,
            role=            row.role,
            is_active=       row.is_active,
            total_requests=  row.total_requests,
            total_tokens=    used,
            total_cost=      round(float(row.total_cost), 6),
            quota_limit=     limit,
            quota_used=      used,
            quota_remaining= remaining,
        )

    all_stats = [_build(r) for r in rows]
    top_users = sorted(all_stats, key=lambda u: u.total_cost, reverse=True)[:10]
    heavy_users = sorted(
        [u for u in all_stats if u.total_tokens >= HEAVY_USAGE_THRESHOLD],
        key=lambda u: u.total_tokens,
        reverse=True,
    )

    return {
        "top_users":   top_users,
        "heavy_users": heavy_users,
    }


# ── GET /admin/models ──────────────────────────────────────────────────────────
@router.get(
    "/models",
    response_model=ModelReportResponse,
    summary="Usage breakdown by provider and model (admin only)",
)
def get_model_report(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    provider_rows = (
        db.query(
            UsageLog.provider                                      .label("provider"),
            func.count(UsageLog.id)                                .label("total_requests"),
            func.coalesce(func.sum(UsageLog.total_tokens),  0)    .label("total_tokens"),
            func.coalesce(func.sum(UsageLog.cost),          0)    .label("total_cost"),
        )
        .group_by(UsageLog.provider)
        .order_by(func.sum(UsageLog.cost).desc())
        .all()
    )

    by_provider = [
        ProviderStat(
            provider=       row.provider,
            total_requests= row.total_requests,
            total_tokens=   int(row.total_tokens),
            total_cost=     round(float(row.total_cost), 6),
        )
        for row in provider_rows
    ]

    model_rows = (
        db.query(
            UsageLog.provider                                      .label("provider"),
            UsageLog.model                                         .label("model_name"),
            func.count(UsageLog.id)                                .label("total_requests"),
            func.coalesce(func.sum(UsageLog.input_tokens),  0)    .label("input_tokens"),
            func.coalesce(func.sum(UsageLog.output_tokens), 0)    .label("output_tokens"),
            func.coalesce(func.sum(UsageLog.total_tokens),  0)    .label("total_tokens"),
            func.coalesce(func.sum(UsageLog.cost),          0)    .label("total_cost"),
        )
        .group_by(UsageLog.provider, UsageLog.model)
        .order_by(func.sum(UsageLog.cost).desc())
        .all()
    )

    by_model = [
        ModelStat(
            provider=       row.provider,
            model_name=     row.model_name or "unknown",
            total_requests= row.total_requests,
            input_tokens=   int(row.input_tokens),
            output_tokens=  int(row.output_tokens),
            total_tokens=   int(row.total_tokens),
            total_cost=     round(float(row.total_cost), 6),
        )
        for row in model_rows
    ]

    return {
        "by_provider": by_provider,
        "by_model":    by_model,
    }
