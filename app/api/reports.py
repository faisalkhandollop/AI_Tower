"""
api/reports.py - Usage Reports (Daily / Weekly / Monthly)

Routes:
    GET /reports/daily              → Daily report for today or a specific date
    GET /reports/weekly             → Weekly report
    GET /reports/monthly            → Monthly report
    GET /reports/team/{team_id}     → Team usage report
    GET /reports/insights           → Routing insights (most expensive team, savings, etc.)
    POST /reports/generate          → Generate and cache a report
"""

from datetime import datetime, timezone, timedelta, date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, and_

from app.db.database import get_db
from app.db.models import UsageLog, User, Team, UserTeam, UsageReport
from app.services.auth import require_admin

router = APIRouter(prefix="/reports", tags=["Usage Reports"])

FREE_PROVIDERS    = {"groq"}
LOW_COST_PROVIDERS = {"openrouter"}
PREMIUM_PROVIDERS = {"claude", "openai", "gemini"}


def _date_range(start: datetime, end: datetime):
    return and_(UsageLog.created_at >= start, UsageLog.created_at < end)


def _aggregate(db: Session, start: datetime, end: datetime, scope_filter=None) -> dict:
    q = db.query(
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.input_tokens), 0).label("input_tokens"),
        func.coalesce(func.sum(UsageLog.output_tokens), 0).label("output_tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
    ).filter(_date_range(start, end))
    if scope_filter is not None:
        q = q.filter(scope_filter)
    r = q.one()
    return {
        "total_requests":      r.requests,
        "total_tokens":        int(r.tokens),
        "total_input_tokens":  int(r.input_tokens),
        "total_output_tokens": int(r.output_tokens),
        "total_cost_usd":      round(float(r.cost), 4),
    }


def _provider_breakdown(db: Session, start: datetime, end: datetime, scope_filter=None) -> list:
    q = db.query(
        UsageLog.provider,
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
    ).filter(_date_range(start, end))
    if scope_filter is not None:
        q = q.filter(scope_filter)
    rows = q.group_by(UsageLog.provider).order_by(func.sum(UsageLog.cost).desc()).all()
    return [{"provider": r.provider, "requests": r.requests,
             "tokens": int(r.tokens), "cost_usd": round(float(r.cost), 4)} for r in rows]


def _model_breakdown(db: Session, start: datetime, end: datetime, scope_filter=None) -> list:
    q = db.query(
        UsageLog.model,
        UsageLog.provider,
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
    ).filter(_date_range(start, end))
    if scope_filter is not None:
        q = q.filter(scope_filter)
    rows = q.group_by(UsageLog.model, UsageLog.provider)\
        .order_by(func.count(UsageLog.id).desc()).limit(10).all()
    return [{"model": r.model, "provider": r.provider, "requests": r.requests,
             "tokens": int(r.tokens), "cost_usd": round(float(r.cost), 4)} for r in rows]


def _build_report(db: Session, start: datetime, end: datetime,
                  period_label: str, scope_filter=None) -> dict:
    agg = _aggregate(db, start, end, scope_filter)
    providers = _provider_breakdown(db, start, end, scope_filter)
    models = _model_breakdown(db, start, end, scope_filter)

    free_cost = sum(p["cost_usd"] for p in providers if p["provider"] in FREE_PROVIDERS)
    paid_cost = sum(p["cost_usd"] for p in providers if p["provider"] not in FREE_PROVIDERS)
    free_reqs = sum(p["requests"] for p in providers if p["provider"] in FREE_PROVIDERS)
    paid_reqs = sum(p["requests"] for p in providers if p["provider"] not in FREE_PROVIDERS)

    return {
        "period":       period_label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **agg,
        "free_requests":   free_reqs,
        "paid_requests":   paid_reqs,
        "free_cost_usd":   round(free_cost, 4),
        "paid_cost_usd":   round(paid_cost, 4),
        "by_provider":     providers,
        "top_models":      models,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/daily", summary="Daily usage report")
def daily_report(
    date_str: Optional[str] = Query(None, description="YYYY-MM-DD (default: today)"),
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            from fastapi import HTTPException
            raise HTTPException(400, "date_str must be YYYY-MM-DD")
    else:
        d = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    start = d
    end   = d + timedelta(days=1)
    return _build_report(db, start, end, d.strftime("%Y-%m-%d"))


@router.get("/weekly", summary="Weekly usage report")
def weekly_report(
    week_offset: int = Query(0, description="0=this week, 1=last week, etc."),
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    today = datetime.now(timezone.utc)
    start_of_week = today - timedelta(days=today.weekday()) - timedelta(weeks=week_offset)
    start = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
    end   = start + timedelta(weeks=1)
    year, week, _ = start.isocalendar()
    return _build_report(db, start, end, f"{year}-W{week:02d}")


@router.get("/monthly", summary="Monthly usage report")
def monthly_report(
    year:  Optional[int] = Query(None, description="Year (default: current)"),
    month: Optional[int] = Query(None, description="Month 1-12 (default: current)"),
    db:    Session = Depends(get_db),
    _admin: User   = Depends(require_admin),
):
    now   = datetime.now(timezone.utc)
    year  = year  or now.year
    month = month or now.month
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    return _build_report(db, start, end, f"{year}-{month:02d}")


@router.get("/team/{team_id}", summary="Team usage report")
def team_report(
    team_id:   str,
    days: int = Query(30, ge=1, le=365),
    db:   Session = Depends(get_db),
    _admin: User  = Depends(require_admin),
):
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        from fastapi import HTTPException
        raise HTTPException(404, "Team not found")

    member_ids = [str(m.user_id) for m in db.query(UserTeam).filter(UserTeam.team_id == team_id).all()]
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    if not member_ids:
        return {"team_id": team_id, "team_name": team.name, "message": "No members in team", "total_requests": 0}

    scope = UsageLog.user_id.in_(member_ids)
    report = _build_report(db, start, end, f"last_{days}_days", scope)

    used_pct    = report["total_tokens"] / team.token_quota * 100 if team.token_quota else 0
    budget_pct  = report["total_cost_usd"] / team.budget_usd * 100 if team.budget_usd else 0

    per_user = db.query(
        UsageLog.user_id,
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
        func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
    ).filter(UsageLog.user_id.in_(member_ids), _date_range(start, end))\
     .group_by(UsageLog.user_id).all()

    return {
        "team_id":     team_id,
        "team_name":   team.name,
        "token_quota": team.token_quota,
        "budget_usd":  team.budget_usd,
        "quota_used_pct":  round(used_pct,  1),
        "budget_used_pct": round(budget_pct, 1),
        **report,
        "per_user": [
            {"user_id": str(r.user_id), "requests": r.requests,
             "tokens": int(r.tokens), "cost_usd": round(float(r.cost), 4)}
            for r in per_user
        ]
    }


@router.get("/insights", summary="Routing insights and savings analysis")
def routing_insights(
    days: int = Query(30, ge=1, le=365),
    db:   Session = Depends(get_db),
    _admin: User  = Depends(require_admin),
):
    """
    Returns:
    - Most expensive team
    - Most used models
    - Savings generated by free model routing
    - Cost comparison: if everything went to Claude vs actual
    """
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Most used models
    top_models = db.query(
        UsageLog.model, UsageLog.provider,
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
    ).filter(_date_range(start, end))\
     .group_by(UsageLog.model, UsageLog.provider)\
     .order_by(func.count(UsageLog.id).desc()).limit(5).all()

    # Most expensive teams (via UserTeam join)
    team_costs = []
    teams = db.query(Team).all()
    for t in teams:
        mids = [str(m.user_id) for m in db.query(UserTeam).filter(UserTeam.team_id == t.id).all()]
        if not mids: continue
        cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0))\
            .filter(UsageLog.user_id.in_(mids), _date_range(start, end)).scalar() or 0
        tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
            .filter(UsageLog.user_id.in_(mids), _date_range(start, end)).scalar() or 0
        team_costs.append({"team": t.name, "team_id": str(t.id),
                           "cost_usd": round(float(cost), 4), "tokens": int(tokens)})
    team_costs.sort(key=lambda x: x["cost_usd"], reverse=True)

    # Savings: free model usage
    free_reqs = db.query(func.count(UsageLog.id))\
        .filter(UsageLog.provider.in_(FREE_PROVIDERS), _date_range(start, end)).scalar() or 0
    free_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
        .filter(UsageLog.provider.in_(FREE_PROVIDERS), _date_range(start, end)).scalar() or 0
    # Estimated savings: avg Claude cost ~$0.003/1k tokens
    estimated_savings = round(int(free_tokens) / 1000 * 0.003, 4)

    # Provider distribution
    prov_dist = db.query(
        UsageLog.provider,
        func.count(UsageLog.id).label("requests"),
        func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
    ).filter(_date_range(start, end)).group_by(UsageLog.provider)\
     .order_by(func.count(UsageLog.id).desc()).all()

    total_reqs = sum(r.requests for r in prov_dist)

    return {
        "period_days":       days,
        "most_expensive_teams": team_costs[:5],
        "most_used_models": [
            {"model": r.model, "provider": r.provider,
             "requests": r.requests, "cost_usd": round(float(r.cost), 4)}
            for r in top_models
        ],
        "savings": {
            "free_requests":     free_reqs,
            "free_tokens":       int(free_tokens),
            "estimated_savings_usd": estimated_savings,
            "message": f"Routing {free_reqs} requests to free models saved ~${estimated_savings}",
        },
        "provider_distribution": [
            {"provider": r.provider, "requests": r.requests,
             "pct": round(r.requests / total_reqs * 100, 1) if total_reqs else 0,
             "cost_usd": round(float(r.cost), 4)}
            for r in prov_dist
        ],
    }
