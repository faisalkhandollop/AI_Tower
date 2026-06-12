"""
api/provider_health.py - Provider Health Monitoring

Routes:
    GET  /providers/health          → Current health status of all providers
    GET  /providers/health/{name}   → Single provider detailed health
    POST /providers/health/check    → Trigger live health check now
    GET  /providers/dashboard       → Full provider dashboard (status + metrics)
    GET  /providers/uptime          → Uptime % per provider (last 24h / 7d)
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import ProviderHealth, UsageLog, User
from app.services.auth import require_admin
from app.providers import groq, claude, openai, gemini, openrouter

router = APIRouter(prefix="/providers", tags=["Provider Health"])

PROVIDERS = {
    "groq":       groq,
    "claude":     claude,
    "openai":     openai,
    "gemini":     gemini,
    "openrouter": openrouter,
}

PROVIDER_COST_TIER = {
    "groq":       "free",
    "openrouter": "low",
    "gemini":     "medium",
    "openai":     "medium",
    "claude":     "high",
}


async def _check_provider(name: str, module) -> dict:
    start = time.monotonic()
    healthy = False
    error = None
    try:
        healthy = await asyncio.wait_for(module.health_check(), timeout=10)
    except Exception as e:
        error = str(e)
    latency_ms = int((time.monotonic() - start) * 1000)
    return {
        "provider":   name,
        "is_healthy": healthy,
        "latency_ms": latency_ms,
        "error":      error,
    }


async def _check_all() -> list[dict]:
    tasks = [_check_provider(name, mod) for name, mod in PROVIDERS.items()]
    return await asyncio.gather(*tasks)


def _save_health_records(results: list[dict], db: Session):
    for r in results:
        db.add(ProviderHealth(
            provider      = r["provider"],
            is_healthy    = r["is_healthy"],
            latency_ms    = r["latency_ms"],
            error_message = r.get("error"),
        ))
    db.commit()


def _get_stats(provider: str, hours: int, db: Session) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(ProviderHealth)
        .filter(ProviderHealth.provider == provider, ProviderHealth.checked_at >= since)
        .all()
    )
    if not rows:
        return {"uptime_pct": None, "avg_latency_ms": None, "checks": 0}

    healthy = sum(1 for r in rows if r.is_healthy)
    latencies = [r.latency_ms for r in rows if r.latency_ms is not None]
    return {
        "uptime_pct":    round(healthy / len(rows) * 100, 1),
        "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "checks":        len(rows),
        "failures":      len(rows) - healthy,
    }


def _get_usage_stats(provider: str, db: Session) -> dict:
    result = (
        db.query(
            func.count(UsageLog.id).label("total"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0).label("tokens"),
            func.coalesce(func.sum(UsageLog.cost), 0).label("cost"),
            func.avg(UsageLog.total_tokens).label("avg_tokens"),
        )
        .filter(UsageLog.provider == provider)
        .one()
    )
    return {
        "total_requests": result.total,
        "total_tokens":   int(result.tokens),
        "total_cost_usd": round(float(result.cost), 4),
        "avg_tokens_per_request": round(float(result.avg_tokens), 0) if result.avg_tokens else 0,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/health", summary="Current health of all providers")
def get_all_health(
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    out = []
    for provider in PROVIDERS:
        latest = (
            db.query(ProviderHealth)
            .filter(ProviderHealth.provider == provider)
            .order_by(ProviderHealth.checked_at.desc())
            .first()
        )
        stats_24h = _get_stats(provider, 24, db)
        out.append({
            "provider":       provider,
            "cost_tier":      PROVIDER_COST_TIER.get(provider, "unknown"),
            "status":         "healthy" if (latest and latest.is_healthy) else "unknown" if not latest else "unhealthy",
            "last_latency_ms": latest.latency_ms if latest else None,
            "last_checked":   str(latest.checked_at) if latest else None,
            "uptime_24h_pct": stats_24h["uptime_pct"],
            "avg_latency_24h_ms": stats_24h["avg_latency_ms"],
            "failures_24h":   stats_24h.get("failures", 0),
        })
    overall = "healthy" if any(p["status"] == "healthy" for p in out) else "degraded"
    return {"overall_status": overall, "providers": out}


@router.get("/health/{provider_name}", summary="Single provider detailed health")
def get_provider_health(
    provider_name: str,
    db:            Session = Depends(get_db),
    _admin:        User    = Depends(require_admin),
):
    if provider_name not in PROVIDERS:
        raise HTTPException(404, f"Unknown provider: {provider_name}")

    latest = (
        db.query(ProviderHealth)
        .filter(ProviderHealth.provider == provider_name)
        .order_by(ProviderHealth.checked_at.desc())
        .first()
    )
    recent = (
        db.query(ProviderHealth)
        .filter(ProviderHealth.provider == provider_name)
        .order_by(ProviderHealth.checked_at.desc())
        .limit(20)
        .all()
    )

    return {
        "provider":    provider_name,
        "cost_tier":   PROVIDER_COST_TIER.get(provider_name),
        "current_status": "healthy" if (latest and latest.is_healthy) else "unknown" if not latest else "unhealthy",
        "last_checked":   str(latest.checked_at) if latest else None,
        "last_latency_ms": latest.latency_ms if latest else None,
        "stats_1h":  _get_stats(provider_name, 1, db),
        "stats_24h": _get_stats(provider_name, 24, db),
        "stats_7d":  _get_stats(provider_name, 168, db),
        "usage":     _get_usage_stats(provider_name, db),
        "history": [
            {
                "checked_at": str(r.checked_at),
                "is_healthy": r.is_healthy,
                "latency_ms": r.latency_ms,
                "error":      r.error_message,
            }
            for r in recent
        ],
    }


@router.post("/health/check", summary="Trigger live health check for all providers")
async def trigger_health_check(
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    results = await _check_all()
    _save_health_records(results, db)
    return {
        "message":    "Health check completed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "results": [
            {
                "provider":   r["provider"],
                "status":     "healthy" if r["is_healthy"] else "unhealthy",
                "latency_ms": r["latency_ms"],
                "error":      r.get("error"),
            }
            for r in results
        ],
    }


@router.get("/dashboard", summary="Full provider dashboard")
def get_provider_dashboard(
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    """Returns complete provider metrics: status, health, latency, success rate, usage."""
    dashboard = []
    for provider in PROVIDERS:
        stats_24h = _get_stats(provider, 24, db)
        usage     = _get_usage_stats(provider, db)
        latest = (
            db.query(ProviderHealth)
            .filter(ProviderHealth.provider == provider)
            .order_by(ProviderHealth.checked_at.desc())
            .first()
        )
        dashboard.append({
            "provider":          provider,
            "cost_tier":         PROVIDER_COST_TIER.get(provider, "unknown"),
            "status":            "healthy" if (latest and latest.is_healthy) else "unknown" if not latest else "unhealthy",
            "response_time_ms":  latest.latency_ms if latest else None,
            "uptime_24h_pct":    stats_24h.get("uptime_pct"),
            "success_rate_pct":  stats_24h.get("uptime_pct"),
            "avg_latency_ms":    stats_24h.get("avg_latency_ms"),
            "total_requests":    usage["total_requests"],
            "total_cost_usd":    usage["total_cost_usd"],
            "total_tokens":      usage["total_tokens"],
        })

    total_req = sum(p["total_requests"] for p in dashboard)
    total_cost = sum(p["total_cost_usd"] for p in dashboard)
    healthy_count = sum(1 for p in dashboard if p["status"] == "healthy")

    return {
        "summary": {
            "total_providers":   len(PROVIDERS),
            "healthy_providers": healthy_count,
            "total_requests":    total_req,
            "total_cost_usd":    round(total_cost, 4),
        },
        "providers": sorted(dashboard, key=lambda x: x["total_requests"], reverse=True),
    }


@router.get("/uptime", summary="Provider uptime percentages")
def get_uptime(
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    result = {}
    for provider in PROVIDERS:
        result[provider] = {
            "1h":  _get_stats(provider, 1,   db),
            "24h": _get_stats(provider, 24,  db),
            "7d":  _get_stats(provider, 168, db),
            "30d": _get_stats(provider, 720, db),
        }
    return {"uptime": result}
