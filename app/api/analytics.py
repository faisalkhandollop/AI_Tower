"""
app/api/analytics.py — Phase 4 Executive Analytics
====================================================
All endpoints are Super Admin only and operate at the platform level.
They NEVER expose individual user conversations, prompts, or responses —
only aggregate counts, costs, and usage statistics in line with the
privacy model defined in the architecture document.

Routes:
    GET  /analytics/platform          → Platform-wide KPI summary
    GET  /analytics/providers         → Usage + cost breakdown by provider & model
    GET  /analytics/organizations     → Per-org aggregate stats (name, plan, usage, cost)
    GET  /analytics/trends            → Time-series data for charts (daily / weekly / monthly)
    GET  /analytics/benchmarks        → Model latency / cost / reliability benchmarks
    POST /analytics/benchmarks/refresh → Re-compute benchmarks from routing_logs (on-demand)
    GET  /analytics/dashboard         → Full dashboard payload (all widgets in one call)

    # Widget layout preferences
    GET  /analytics/widgets           → Fetch current admin's widget config
    PUT  /analytics/widgets           → Save widget layout

    # Snapshot management (background-job-style, callable manually)
    POST /analytics/snapshots/compute → Compute & store a snapshot for a given period
    GET  /analytics/snapshots         → List stored snapshots
"""

import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, cast, String

from app.db.database import get_db
from app.db.models import (
    Organization, UsageLog, User,
    UsageReport, Team, Department,
)
from app.db.models_enterprise import (
    Subscription, SubscriptionPlan, AuditLog,
)
from app.db.models_billing import Invoice, Payment
from app.db.models_routing import RoutingLog
from app.db.models_analytics import (
    CompanyUsageSummary, AnalyticsSnapshot,
    DashboardWidget, ModelBenchmark, SNAPSHOT_TYPES,
)
from app.services.rbac import require_super_admin

router = APIRouter(prefix="/analytics", tags=["Executive Analytics"])


# =============================================================================
# Helpers
# =============================================================================

def _date_range(days: int) -> datetime:
    """Return UTC datetime `days` ago from now."""
    return datetime.now(timezone.utc) - timedelta(days=days)


def _safe_float(v) -> float:
    try:
        return round(float(v), 6) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _org_plan(org_id, db: Session) -> dict:
    """Return plan name and tier for an org, or defaults if unsubscribed."""
    sub = db.query(Subscription).filter(
        Subscription.organization_id == org_id
    ).first()
    if sub:
        plan = db.query(SubscriptionPlan).filter(
            SubscriptionPlan.id == sub.plan_id
        ).first()
        if plan:
            return {"plan": plan.name, "tier": plan.tier, "status": sub.status}
    return {"plan": "none", "tier": "free", "status": "none"}


# =============================================================================
# Schemas
# =============================================================================

class WidgetConfig(BaseModel):
    widget_key: str = Field(..., min_length=1, max_length=100)
    position:   int = Field(0, ge=0)
    is_visible: bool = True
    config:     dict = Field(default_factory=dict)


class SaveWidgetsRequest(BaseModel):
    widgets: List[WidgetConfig]


class ComputeSnapshotRequest(BaseModel):
    snapshot_type: str = Field(..., description="daily | weekly | monthly | quarterly")
    period_label:  str = Field(..., description="e.g. '2026-06-12', '2026-W22', '2026-06', '2026-Q2'")
    scope:         str = Field("platform", description="'platform' or an org UUID")


class BenchmarkRefreshRequest(BaseModel):
    sample_window: str = Field("24h", description="1h | 6h | 24h | 7d")
    provider:      Optional[str] = None   # None = all providers
    model:         Optional[str] = None   # None = all models


# =============================================================================
# GET /analytics/platform
# Platform-wide KPI summary for the Super Admin home card.
# Returns ONLY aggregate numbers — no user content.
# =============================================================================

@router.get(
    "/platform",
    summary="Platform-wide KPI summary (Super Admin only)",
)
def platform_summary(
    date_range_days: int = Query(30, ge=1, le=365, description="Look-back window in days"),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    since = _date_range(date_range_days)

    # ── Org counts ────────────────────────────────────────────────────────────
    total_orgs  = db.query(func.count(Organization.id)).scalar() or 0
    active_orgs = db.query(func.count(Organization.id)).filter(
        Organization.is_active == True
    ).scalar() or 0
    new_orgs = db.query(func.count(Organization.id)).filter(
        Organization.created_at >= since
    ).scalar() or 0

    # ── User counts ───────────────────────────────────────────────────────────
    total_users  = db.query(func.count(User.id)).scalar() or 0
    active_users = db.query(func.count(User.id)).filter(User.is_active == True).scalar() or 0

    # ── Request / token / cost aggregates ─────────────────────────────────────
    agg = db.query(
        func.count(UsageLog.id)                              .label("total_requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0)   .label("total_tokens"),
        func.coalesce(func.sum(UsageLog.input_tokens), 0)   .label("input_tokens"),
        func.coalesce(func.sum(UsageLog.output_tokens), 0)  .label("output_tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0)           .label("total_cost"),
    ).filter(UsageLog.created_at >= since).one()

    total_requests = agg.total_requests
    total_cost     = _safe_float(agg.total_cost)
    avg_cost       = round(total_cost / total_requests, 8) if total_requests else 0.0

    # ── Revenue / MRR ─────────────────────────────────────────────────────────
    mrr_rows = (
        db.query(SubscriptionPlan.price_monthly_usd)
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.status.in_(["active", "trialing"]))
        .all()
    )
    mrr = float(sum(row[0] for row in mrr_rows)) if mrr_rows else 0.0
    arr = mrr * 12

    # Revenue collected (payments) in the look-back window
    revenue_window = _safe_float(
        db.query(func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "succeeded", Payment.paid_at >= since)
        .scalar()
    )

    # ── Pending invoices ──────────────────────────────────────────────────────
    pending_amount = _safe_float(
        db.query(func.coalesce(func.sum(Invoice.amount_due), 0))
        .filter(Invoice.status.in_(["sent", "partially_paid", "overdue"]))
        .scalar()
    )

    # ── Provider health snapshot ──────────────────────────────────────────────
    provider_counts = (
        db.query(UsageLog.provider, func.count(UsageLog.id).label("cnt"))
        .filter(UsageLog.created_at >= since)
        .group_by(UsageLog.provider)
        .order_by(func.count(UsageLog.id).desc())
        .all()
    )
    top_providers = [{"provider": r.provider, "requests": r.cnt} for r in provider_counts[:5]]

    return {
        "date_range_days": date_range_days,
        "organizations": {
            "total":  total_orgs,
            "active": active_orgs,
            "new":    new_orgs,
        },
        "users": {
            "total":  total_users,
            "active": active_users,
        },
        "usage": {
            "total_requests":     total_requests,
            "total_tokens":       int(agg.total_tokens),
            "input_tokens":       int(agg.input_tokens),
            "output_tokens":      int(agg.output_tokens),
            "total_cost_usd":     total_cost,
            "avg_cost_per_request": avg_cost,
        },
        "revenue": {
            "mrr":              round(mrr, 2),
            "arr":              round(arr, 2),
            "collected_window": round(revenue_window, 2),
            "pending":          round(pending_amount, 2),
        },
        "top_providers": top_providers,
    }


# =============================================================================
# GET /analytics/providers
# Breakdown by provider and by model.
# =============================================================================

@router.get(
    "/providers",
    summary="Usage & cost breakdown by provider and model (Super Admin only)",
)
def provider_analytics(
    date_range_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    since = _date_range(date_range_days)

    # ── By Provider ───────────────────────────────────────────────────────────
    prov_rows = (
        db.query(
            UsageLog.provider,
            func.count(UsageLog.id)                              .label("requests"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0)   .label("tokens"),
            func.coalesce(func.sum(UsageLog.cost), 0)           .label("cost"),
            func.avg(UsageLog.total_tokens)                      .label("avg_tokens"),
        )
        .filter(UsageLog.created_at >= since)
        .group_by(UsageLog.provider)
        .order_by(func.sum(UsageLog.cost).desc())
        .all()
    )

    by_provider = [
        {
            "provider":        r.provider,
            "total_requests":  r.requests,
            "total_tokens":    int(r.tokens),
            "total_cost_usd":  _safe_float(r.cost),
            "avg_tokens_per_request": round(float(r.avg_tokens or 0), 1),
        }
        for r in prov_rows
    ]

    # ── By Model ──────────────────────────────────────────────────────────────
    model_rows = (
        db.query(
            UsageLog.provider,
            UsageLog.model,
            func.count(UsageLog.id)                              .label("requests"),
            func.coalesce(func.sum(UsageLog.input_tokens), 0)   .label("input_tokens"),
            func.coalesce(func.sum(UsageLog.output_tokens), 0)  .label("output_tokens"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0)   .label("tokens"),
            func.coalesce(func.sum(UsageLog.cost), 0)           .label("cost"),
        )
        .filter(UsageLog.created_at >= since)
        .group_by(UsageLog.provider, UsageLog.model)
        .order_by(func.sum(UsageLog.cost).desc())
        .all()
    )

    by_model = [
        {
            "provider":       r.provider,
            "model":          r.model or "unknown",
            "total_requests": r.requests,
            "input_tokens":   int(r.input_tokens),
            "output_tokens":  int(r.output_tokens),
            "total_tokens":   int(r.tokens),
            "total_cost_usd": _safe_float(r.cost),
        }
        for r in model_rows
    ]

    # ── Cost share % ─────────────────────────────────────────────────────────
    grand_cost = sum(r["total_cost_usd"] for r in by_provider)
    for r in by_provider:
        r["cost_share_pct"] = round(
            (r["total_cost_usd"] / grand_cost * 100) if grand_cost else 0, 2
        )

    # ── Model benchmarks (latest per model) ───────────────────────────────────
    bench_subq = (
        db.query(
            ModelBenchmark.provider,
            ModelBenchmark.model,
            func.max(ModelBenchmark.sampled_at).label("latest"),
        )
        .group_by(ModelBenchmark.provider, ModelBenchmark.model)
        .subquery()
    )
    bench_rows = (
        db.query(ModelBenchmark)
        .join(
            bench_subq,
            (ModelBenchmark.provider == bench_subq.c.provider)
            & (ModelBenchmark.model == bench_subq.c.model)
            & (ModelBenchmark.sampled_at == bench_subq.c.latest),
        )
        .all()
    )
    benchmarks = [
        {
            "provider":       b.provider,
            "model":          b.model,
            "avg_latency_ms": b.avg_latency_ms,
            "p95_latency_ms": b.p95_latency_ms,
            "error_rate":     b.error_rate,
            "sample_window":  b.sample_window,
            "sampled_at":     b.sampled_at.isoformat() if b.sampled_at else None,
        }
        for b in bench_rows
    ]

    return {
        "date_range_days": date_range_days,
        "by_provider": by_provider,
        "by_model":    by_model,
        "benchmarks":  benchmarks,
    }


# =============================================================================
# GET /analytics/organizations
# Per-org aggregate: name, plan, users, requests, tokens, cost.
# Privacy: no conversations, prompts, or user identities exposed.
# =============================================================================

@router.get(
    "/organizations",
    summary="Per-org aggregate analytics (Super Admin only — no user content)",
)
def organization_analytics(
    date_range_days: int = Query(30, ge=1, le=365),
    sort_by: str = Query("cost", description="cost | requests | tokens | users"),
    limit:   int = Query(50, ge=1, le=200),
    offset:  int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    since = _date_range(date_range_days)

    # Fetch all orgs
    orgs = db.query(Organization).order_by(Organization.name).all()

    results = []
    for org in orgs:
        # Usage aggregate — filtered by org via usage_logs.organization_id
        # Note: usage_logs is the base table; org scoping uses UsageLog.user_id
        # joined back to users. We use the org's user set to scope.
        user_ids = [
            str(u.id)
            for u in db.query(User.id).filter(
                User.id.in_(
                    db.query(User.id).filter(
                        # Users belonging to this org via user_teams
                        User.id.in_(
                            db.query(
                                __import__('app.db.models', fromlist=['UserTeam']).UserTeam.user_id
                            ).filter(
                                __import__('app.db.models', fromlist=['UserTeam']).UserTeam.org_id == org.id
                            )
                        )
                    )
                )
            ).all()
        ]

        if user_ids:
            agg = db.query(
                func.count(UsageLog.id)                             .label("requests"),
                func.coalesce(func.sum(UsageLog.total_tokens), 0)  .label("tokens"),
                func.coalesce(func.sum(UsageLog.cost), 0)          .label("cost"),
                func.count(func.distinct(UsageLog.user_id))        .label("active_users"),
            ).filter(
                UsageLog.user_id.in_(user_ids),
                UsageLog.created_at >= since,
            ).one()
        else:
            class _Z:
                requests = 0; tokens = 0; cost = 0; active_users = 0
            agg = _Z()

        plan_info = _org_plan(org.id, db)
        total_users = db.query(func.count(
            __import__('app.db.models', fromlist=['UserTeam']).UserTeam.id
        )).filter(
            __import__('app.db.models', fromlist=['UserTeam']).UserTeam.org_id == org.id
        ).scalar() or 0

        results.append({
            "organization_id":   str(org.id),
            "organization_name": org.name,
            "slug":              org.slug,
            "is_active":         org.is_active,
            "created_at":        org.created_at.isoformat() if org.created_at else None,
            "plan":              plan_info["plan"],
            "tier":              plan_info["tier"],
            "subscription_status": plan_info["status"],
            "total_users":       total_users,
            "active_users":      agg.active_users,
            "total_requests":    agg.requests,
            "total_tokens":      int(agg.tokens),
            "total_cost_usd":    _safe_float(agg.cost),
        })

    # Sort
    sort_key_map = {
        "cost":     lambda x: x["total_cost_usd"],
        "requests": lambda x: x["total_requests"],
        "tokens":   lambda x: x["total_tokens"],
        "users":    lambda x: x["total_users"],
    }
    key_fn = sort_key_map.get(sort_by, sort_key_map["cost"])
    results.sort(key=key_fn, reverse=True)

    total_count = len(results)
    paged = results[offset: offset + limit]

    return {
        "date_range_days": date_range_days,
        "total":           total_count,
        "limit":           limit,
        "offset":          offset,
        "organizations":   paged,
        "summary": {
            "total_cost_usd":  round(sum(r["total_cost_usd"]  for r in results), 4),
            "total_requests":  sum(r["total_requests"] for r in results),
            "total_tokens":    sum(r["total_tokens"]   for r in results),
        },
    }


# =============================================================================
# GET /analytics/trends
# Daily / weekly / monthly time-series aggregates.
# =============================================================================

@router.get(
    "/trends",
    summary="Time-series trend data for charts (Super Admin only)",
)
def analytics_trends(
    granularity: str = Query("daily", description="daily | weekly | monthly"),
    date_range_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    if granularity not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "granularity must be 'daily', 'weekly', or 'monthly'")

    since = _date_range(date_range_days)

    # Prefer pre-computed snapshots
    snap_type_map = {"daily": "daily", "weekly": "weekly", "monthly": "monthly"}
    snaps = (
        db.query(AnalyticsSnapshot)
        .filter(
            AnalyticsSnapshot.snapshot_type == snap_type_map[granularity],
            AnalyticsSnapshot.scope == "platform",
            AnalyticsSnapshot.generated_at >= since,
        )
        .order_by(AnalyticsSnapshot.period_label.asc())
        .all()
    )

    if snaps:
        return {
            "granularity":     granularity,
            "date_range_days": date_range_days,
            "source":          "snapshots",
            "data_points":     [
                {"period": s.period_label, **s.data}
                for s in snaps
            ],
        }

    # Fallback: live aggregation bucketed by day
    rows = (
        db.query(
            func.date_trunc("day", UsageLog.created_at).label("bucket"),
            func.count(UsageLog.id)                             .label("requests"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0)  .label("tokens"),
            func.coalesce(func.sum(UsageLog.cost), 0)          .label("cost"),
            func.count(func.distinct(UsageLog.user_id))        .label("active_users"),
        )
        .filter(UsageLog.created_at >= since)
        .group_by(func.date_trunc("day", UsageLog.created_at))
        .order_by(func.date_trunc("day", UsageLog.created_at).asc())
        .all()
    )

    data_points = [
        {
            "period":       r.bucket.strftime("%Y-%m-%d") if r.bucket else None,
            "requests":     r.requests,
            "tokens":       int(r.tokens),
            "cost_usd":     _safe_float(r.cost),
            "active_users": r.active_users,
        }
        for r in rows
    ]

    return {
        "granularity":     granularity,
        "date_range_days": date_range_days,
        "source":          "live",
        "data_points":     data_points,
    }


# =============================================================================
# GET /analytics/benchmarks
# Latest model benchmarks per (provider, model).
# =============================================================================

@router.get(
    "/benchmarks",
    summary="Model latency, cost & reliability benchmarks (Super Admin only)",
)
def list_benchmarks(
    provider:      Optional[str] = Query(None),
    sample_window: Optional[str] = Query(None, description="1h | 6h | 24h | 7d"),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    # Latest benchmark per (provider, model)
    subq = (
        db.query(
            ModelBenchmark.provider,
            ModelBenchmark.model,
            func.max(ModelBenchmark.sampled_at).label("latest"),
        )
        .group_by(ModelBenchmark.provider, ModelBenchmark.model)
        .subquery()
    )

    q = (
        db.query(ModelBenchmark)
        .join(
            subq,
            (ModelBenchmark.provider == subq.c.provider)
            & (ModelBenchmark.model == subq.c.model)
            & (ModelBenchmark.sampled_at == subq.c.latest),
        )
    )
    if provider:
        q = q.filter(ModelBenchmark.provider == provider)
    if sample_window:
        q = q.filter(ModelBenchmark.sample_window == sample_window)

    benchmarks = q.order_by(ModelBenchmark.provider, ModelBenchmark.model).all()

    return {
        "benchmarks": [
            {
                "id":                   str(b.id),
                "provider":             b.provider,
                "model":                b.model,
                "sample_window":        b.sample_window,
                "sampled_at":           b.sampled_at.isoformat() if b.sampled_at else None,
                "avg_latency_ms":       b.avg_latency_ms,
                "p50_latency_ms":       b.p50_latency_ms,
                "p95_latency_ms":       b.p95_latency_ms,
                "p99_latency_ms":       b.p99_latency_ms,
                "avg_cost_per_req_usd": _safe_float(b.avg_cost_per_req),
                "avg_input_cost_per_1k": _safe_float(b.avg_input_cost),
                "avg_output_cost_per_1k": _safe_float(b.avg_output_cost),
                "total_requests":       b.total_requests,
                "error_count":          b.error_count,
                "error_rate":           b.error_rate,
                "avg_tokens_in":        b.avg_tokens_in,
                "avg_tokens_out":       b.avg_tokens_out,
                "routing_selections":   b.routing_selections,
                "fallback_count":       b.fallback_count,
            }
            for b in benchmarks
        ]
    }


# =============================================================================
# POST /analytics/benchmarks/refresh
# Re-compute benchmarks from routing_logs for the given window.
# =============================================================================

@router.post(
    "/benchmarks/refresh",
    summary="Re-compute model benchmarks from routing_logs (Super Admin only)",
    status_code=201,
)
def refresh_benchmarks(
    payload: BenchmarkRefreshRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    window_hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 168}
    hours = window_hours.get(payload.sample_window)
    if not hours:
        raise HTTPException(400, f"Invalid sample_window '{payload.sample_window}'. Use 1h | 6h | 24h | 7d")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    q = db.query(
        RoutingLog.selected_provider.label("provider"),
        RoutingLog.selected_model.label("model"),
        func.count(RoutingLog.id)                               .label("total"),
        func.avg(RoutingLog.latency_ms)                        .label("avg_latency"),
        func.avg(RoutingLog.cost_usd)                          .label("avg_cost"),
        func.sum(RoutingLog.input_tokens)                      .label("sum_in"),
        func.sum(RoutingLog.output_tokens)                     .label("sum_out"),
        func.count(RoutingLog.id).filter(RoutingLog.status == "error").label("errors"),
        func.sum(RoutingLog.was_fallback.cast(sa_Integer()))   .label("fallbacks"),
        func.count(RoutingLog.id)                              .label("selections"),
    ).filter(
        RoutingLog.created_at >= since,
    ).group_by(
        RoutingLog.selected_provider,
        RoutingLog.selected_model,
    )

    # NOTE: sa_Integer import shortcut
    from sqlalchemy import Integer as sa_Integer  # noqa

    q = db.query(
        RoutingLog.selected_provider.label("provider"),
        RoutingLog.selected_model.label("model"),
        func.count(RoutingLog.id)                              .label("total"),
        func.avg(RoutingLog.latency_ms)                        .label("avg_latency"),
        func.avg(RoutingLog.cost_usd)                          .label("avg_cost"),
        func.coalesce(func.avg(RoutingLog.input_tokens), 0)   .label("avg_in"),
        func.coalesce(func.avg(RoutingLog.output_tokens), 0)  .label("avg_out"),
        func.count(RoutingLog.id).filter(RoutingLog.status == "error").label("errors"),
        func.count(RoutingLog.id).filter(RoutingLog.was_fallback == True).label("fallbacks"),
    ).filter(
        RoutingLog.created_at >= since,
        RoutingLog.selected_provider.isnot(None),
    ).group_by(
        RoutingLog.selected_provider,
        RoutingLog.selected_model,
    )

    if payload.provider:
        q = q.filter(RoutingLog.selected_provider == payload.provider)
    if payload.model:
        q = q.filter(RoutingLog.selected_model == payload.model)

    rows = q.all()
    created = 0

    now = datetime.now(timezone.utc)
    for r in rows:
        total    = r.total or 0
        errors   = r.errors or 0
        avg_lat  = float(r.avg_latency) if r.avg_latency else None
        avg_cost = float(r.avg_cost) if r.avg_cost else None

        bench = ModelBenchmark(
            id=uuid.uuid4(),
            provider=r.provider,
            model=r.model or "unknown",
            sampled_at=now,
            sample_window=payload.sample_window,
            avg_latency_ms=avg_lat,
            p50_latency_ms=avg_lat,    # approximation without percentile support here
            p95_latency_ms=None,
            p99_latency_ms=None,
            avg_cost_per_req=avg_cost,
            total_requests=total,
            error_count=errors,
            error_rate=round(errors / total, 4) if total else None,
            avg_tokens_in=float(r.avg_in) if r.avg_in else None,
            avg_tokens_out=float(r.avg_out) if r.avg_out else None,
            routing_selections=total,
            fallback_count=r.fallbacks or 0,
        )
        db.add(bench)
        created += 1

    db.commit()
    return {"message": f"Benchmarks refreshed", "rows_created": created, "sample_window": payload.sample_window}


# =============================================================================
# GET /analytics/dashboard
# Single-call composite payload — all widgets in one response.
# Clients can use this to populate the full Super Admin dashboard.
# =============================================================================

@router.get(
    "/dashboard",
    summary="Full analytics dashboard — all widgets in one call (Super Admin only)",
)
def analytics_dashboard(
    date_range_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    since = _date_range(date_range_days)

    # ── KPI Cards ─────────────────────────────────────────────────────────────
    total_orgs  = db.query(func.count(Organization.id)).scalar() or 0
    active_orgs = db.query(func.count(Organization.id)).filter(Organization.is_active == True).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0

    agg = db.query(
        func.count(UsageLog.id)                             .label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0)  .label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0)          .label("cost"),
    ).filter(UsageLog.created_at >= since).one()

    # ── MRR ───────────────────────────────────────────────────────────────────
    mrr_rows = (
        db.query(SubscriptionPlan.price_monthly_usd)
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.status.in_(["active", "trialing"]))
        .all()
    )
    mrr = round(float(sum(r[0] for r in mrr_rows)), 2) if mrr_rows else 0.0

    # ── Top Orgs by cost ─────────────────────────────────────────────────────
    from app.db.models import UserTeam
    top_orgs_raw = (
        db.query(
            Organization.id,
            Organization.name,
            func.count(UsageLog.id)                            .label("requests"),
            func.coalesce(func.sum(UsageLog.cost), 0)          .label("cost"),
        )
        .join(UserTeam, UserTeam.org_id == Organization.id)
        .join(UsageLog, UsageLog.user_id == UserTeam.user_id)
        .filter(UsageLog.created_at >= since)
        .group_by(Organization.id, Organization.name)
        .order_by(func.sum(UsageLog.cost).desc())
        .limit(10)
        .all()
    )
    top_orgs = [
        {
            "organization_id":   str(r.id),
            "organization_name": r.name,
            "total_requests":    r.requests,
            "total_cost_usd":    _safe_float(r.cost),
        }
        for r in top_orgs_raw
    ]

    # ── Top Models by cost ────────────────────────────────────────────────────
    top_models_raw = (
        db.query(
            UsageLog.provider,
            UsageLog.model,
            func.count(UsageLog.id)                            .label("requests"),
            func.coalesce(func.sum(UsageLog.cost), 0)          .label("cost"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0)  .label("tokens"),
        )
        .filter(UsageLog.created_at >= since)
        .group_by(UsageLog.provider, UsageLog.model)
        .order_by(func.sum(UsageLog.cost).desc())
        .limit(10)
        .all()
    )
    top_models = [
        {
            "provider":       r.provider,
            "model":          r.model or "unknown",
            "total_requests": r.requests,
            "total_tokens":   int(r.tokens),
            "total_cost_usd": _safe_float(r.cost),
        }
        for r in top_models_raw
    ]

    # ── Subscription breakdown by plan ────────────────────────────────────────
    plan_counts = (
        db.query(
            SubscriptionPlan.name,
            SubscriptionPlan.tier,
            func.count(Subscription.id).label("cnt"),
        )
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.status.in_(["active", "trialing"]))
        .group_by(SubscriptionPlan.name, SubscriptionPlan.tier)
        .all()
    )
    subscriptions_by_plan = [
        {"plan": r.name, "tier": r.tier, "org_count": r.cnt}
        for r in plan_counts
    ]

    # ── Recent audit events (platform-level only) ─────────────────────────────
    recent_audits = (
        db.query(AuditLog)
        .filter(AuditLog.organization_id.is_(None))   # platform-level only
        .order_by(AuditLog.timestamp.desc())
        .limit(10)
        .all()
    )
    audit_feed = [
        {
            "action":       a.action,
            "actor_role":   a.actor_role,
            "resource_type": a.resource_type,
            "timestamp":    a.timestamp.isoformat() if a.timestamp else None,
            "ip_address":   a.ip_address,
        }
        for a in recent_audits
    ]

    return {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "date_range_days": date_range_days,
        "kpi": {
            "total_organizations": total_orgs,
            "active_organizations": active_orgs,
            "total_users":         total_users,
            "total_requests":      agg.requests,
            "total_tokens":        int(agg.tokens),
            "total_cost_usd":      _safe_float(agg.cost),
            "mrr_usd":             mrr,
            "arr_usd":             round(mrr * 12, 2),
        },
        "top_organizations":     top_orgs,
        "top_models":            top_models,
        "subscriptions_by_plan": subscriptions_by_plan,
        "platform_audit_feed":   audit_feed,
    }


# =============================================================================
# Widget preferences
# =============================================================================

@router.get(
    "/widgets",
    summary="Fetch current admin's dashboard widget config",
)
def get_widgets(
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    admin_id = _sa.get("sub")
    widgets = (
        db.query(DashboardWidget)
        .filter(DashboardWidget.admin_id == admin_id)
        .order_by(DashboardWidget.position.asc())
        .all()
    )
    return {
        "widgets": [
            {
                "id":         str(w.id),
                "widget_key": w.widget_key,
                "position":   w.position,
                "is_visible": w.is_visible,
                "config":     w.config or {},
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
            for w in widgets
        ]
    }


@router.put(
    "/widgets",
    summary="Save dashboard widget layout preferences",
)
def save_widgets(
    payload: SaveWidgetsRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    admin_id = _sa.get("sub")
    if not admin_id:
        raise HTTPException(401, "Cannot identify admin from token")

    saved = []
    for w in payload.widgets:
        existing = (
            db.query(DashboardWidget)
            .filter(
                DashboardWidget.admin_id == admin_id,
                DashboardWidget.widget_key == w.widget_key,
            )
            .first()
        )
        if existing:
            existing.position   = w.position
            existing.is_visible = w.is_visible
            existing.config     = w.config
        else:
            existing = DashboardWidget(
                id=uuid.uuid4(),
                admin_id=admin_id,
                widget_key=w.widget_key,
                position=w.position,
                is_visible=w.is_visible,
                config=w.config,
            )
            db.add(existing)
        saved.append(existing)

    db.commit()
    return {"message": f"Saved {len(saved)} widget(s)"}


# =============================================================================
# Snapshots
# =============================================================================

@router.get(
    "/snapshots",
    summary="List stored analytics snapshots (Super Admin only)",
)
def list_snapshots(
    snapshot_type: Optional[str] = Query(None, description="daily | weekly | monthly | quarterly"),
    scope:         Optional[str] = Query(None),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    q = db.query(AnalyticsSnapshot)
    if snapshot_type:
        if snapshot_type not in SNAPSHOT_TYPES:
            raise HTTPException(400, f"Invalid snapshot_type. Choose from: {', '.join(SNAPSHOT_TYPES)}")
        q = q.filter(AnalyticsSnapshot.snapshot_type == snapshot_type)
    if scope:
        q = q.filter(AnalyticsSnapshot.scope == scope)

    total = q.count()
    snaps = q.order_by(AnalyticsSnapshot.period_label.desc()).offset(offset).limit(limit).all()

    return {
        "total":  total,
        "limit":  limit,
        "offset": offset,
        "snapshots": [
            {
                "id":            str(s.id),
                "snapshot_type": s.snapshot_type,
                "period_label":  s.period_label,
                "scope":         s.scope,
                "data":          s.data,
                "generated_at":  s.generated_at.isoformat() if s.generated_at else None,
            }
            for s in snaps
        ],
    }


@router.post(
    "/snapshots/compute",
    status_code=201,
    summary="Compute & persist an analytics snapshot for a given period (Super Admin only)",
)
def compute_snapshot(
    payload: ComputeSnapshotRequest,
    db: Session = Depends(get_db),
    _sa: dict = Depends(require_super_admin),
):
    if payload.snapshot_type not in SNAPSHOT_TYPES:
        raise HTTPException(400, f"Invalid snapshot_type. Must be one of: {', '.join(SNAPSHOT_TYPES)}")

    # Date window based on snapshot_type
    now = datetime.now(timezone.utc)
    window_map = {"daily": 1, "weekly": 7, "monthly": 30, "quarterly": 90}
    days = window_map[payload.snapshot_type]
    since = now - timedelta(days=days)

    # Aggregate live data
    agg = db.query(
        func.count(UsageLog.id)                             .label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0)  .label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0)          .label("cost"),
        func.count(func.distinct(UsageLog.user_id))        .label("active_users"),
    ).filter(UsageLog.created_at >= since).one()

    total_orgs  = db.query(func.count(Organization.id)).scalar() or 0
    total_users = db.query(func.count(User.id)).scalar() or 0
    new_orgs    = db.query(func.count(Organization.id)).filter(
        Organization.created_at >= since
    ).scalar() or 0

    mrr_rows = (
        db.query(SubscriptionPlan.price_monthly_usd)
        .join(Subscription, Subscription.plan_id == SubscriptionPlan.id)
        .filter(Subscription.status.in_(["active", "trialing"]))
        .all()
    )
    mrr = float(sum(r[0] for r in mrr_rows)) if mrr_rows else 0.0

    # Top providers
    prov_rows = (
        db.query(UsageLog.provider, func.count(UsageLog.id).label("cnt"))
        .filter(UsageLog.created_at >= since)
        .group_by(UsageLog.provider)
        .order_by(func.count(UsageLog.id).desc())
        .limit(5)
        .all()
    )

    snap_data = {
        "total_orgs":      total_orgs,
        "new_orgs":        new_orgs,
        "total_users":     total_users,
        "active_users":    agg.active_users,
        "total_requests":  agg.requests,
        "total_tokens":    int(agg.tokens),
        "total_cost_usd":  _safe_float(agg.cost),
        "mrr_usd":         round(mrr, 2),
        "arr_usd":         round(mrr * 12, 2),
        "top_providers":   [{"provider": r.provider, "requests": r.cnt} for r in prov_rows],
    }

    # Upsert
    existing = (
        db.query(AnalyticsSnapshot)
        .filter(
            AnalyticsSnapshot.snapshot_type == payload.snapshot_type,
            AnalyticsSnapshot.period_label  == payload.period_label,
            AnalyticsSnapshot.scope         == payload.scope,
        )
        .first()
    )

    if existing:
        existing.data         = snap_data
        existing.generated_at = now
        snap = existing
    else:
        snap = AnalyticsSnapshot(
            id=uuid.uuid4(),
            snapshot_type=payload.snapshot_type,
            period_label=payload.period_label,
            scope=payload.scope,
            data=snap_data,
            generated_at=now,
        )
        db.add(snap)

    db.commit()
    db.refresh(snap)

    return {
        "message":      "Snapshot computed and stored",
        "snapshot_id":  str(snap.id),
        "period_label": snap.period_label,
        "data":         snap.data,
    }
