"""
app/services/routing_governance.py
====================================
Phase 3 Routing Governance Service.

Responsibilities:
    1. evaluate_rules()    — check routing_rules before smart router runs
    2. log_routing()       — persist a RoutingLog record after every request
    3. get_analytics()     — aggregate stats for the dashboard
    4. seed_categories()   — insert default prompt_categories on startup
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.db.models_routing import (
    PromptCategory,
    RoutingRule,
    RoutingLog,
    RoutingFeedback,
    DEFAULT_CATEGORIES,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# CATEGORY SEEDING
# ─────────────────────────────────────────────────────────────

_CATEGORY_META = {
    "code_generation":  ("#6366F1", "Tasks that produce executable code"),
    "code_review":      ("#8B5CF6", "Analysis of existing code for bugs or improvements"),
    "data_analysis":    ("#0EA5E9", "Statistical or tabular data interpretation"),
    "document_summary": ("#10B981", "Condensing long-form documents"),
    "creative_writing": ("#F59E0B", "Fiction, poetry, or marketing copy"),
    "translation":      ("#EF4444", "Converting content between languages"),
    "question_answering":("#3B82F6","Direct factual or knowledge lookup"),
    "reasoning":        ("#EC4899", "Multi-step logical or mathematical reasoning"),
    "image_understanding":("#14B8A6","Describing or analysing image content"),
    "general":          ("#6B7280", "Catch-all for uncategorised prompts"),
}


def seed_categories(db: Session) -> None:
    """Idempotent — inserts missing default categories only."""
    for slug in DEFAULT_CATEGORIES:
        exists = db.query(PromptCategory).filter_by(slug=slug).first()
        if not exists:
            color, desc = _CATEGORY_META.get(slug, ("#6B7280", ""))
            db.add(PromptCategory(
                id=uuid.uuid4(),
                name=slug.replace("_", " ").title(),
                slug=slug,
                description=desc,
                color_hex=color,
            ))
    db.commit()
    logger.info("Routing governance: prompt categories seeded.")


# ─────────────────────────────────────────────────────────────
# RULE EVALUATION
# ─────────────────────────────────────────────────────────────

class RuleMatch:
    """Returned when a rule fires."""
    def __init__(self, rule: RoutingRule):
        self.rule_id         = rule.id
        self.action          = rule.action
        self.target_provider = rule.target_provider
        self.target_model    = rule.target_model
        self.fallback_provider = rule.fallback_provider
        self.fallback_model    = rule.fallback_model


def evaluate_rules(
    db: Session,
    *,
    organization_id: Optional[uuid.UUID] = None,
    department_id:   Optional[uuid.UUID] = None,
    category_slug:   Optional[str]       = None,
    complexity:      Optional[str]       = None,
    current_provider:Optional[str]       = None,
    current_model:   Optional[str]       = None,
    projected_cost:  Optional[float]     = None,
) -> Optional[RuleMatch]:
    """
    Evaluate active routing rules in priority order.
    Returns the first matching RuleMatch, or None if no rule fires.

    Rules are scoped:
        - platform-wide (org_id IS NULL)
        - org-scoped    (org_id = caller's org)
        - dept-scoped   (dept_id = caller's dept)

    All matching happens with org/dept scoping first, then platform rules.
    """
    query = (
        db.query(RoutingRule)
        .filter(RoutingRule.is_active == True)
        .filter(
            (RoutingRule.organization_id == None)
            | (RoutingRule.organization_id == organization_id)
        )
        .order_by(RoutingRule.priority.asc())
    )

    rules = query.all()

    for rule in rules:
        # Department filter
        if rule.department_id is not None and rule.department_id != department_id:
            continue

        # Match criteria — all provided criteria must match
        if rule.match_category and rule.match_category != category_slug:
            continue
        if rule.match_complexity and rule.match_complexity != complexity:
            continue
        if rule.match_provider and rule.match_provider != current_provider:
            continue
        if rule.match_model and rule.match_model != current_model:
            continue
        if rule.cost_threshold_usd is not None:
            if projected_cost is None or projected_cost <= float(rule.cost_threshold_usd):
                continue

        logger.info(
            "Routing rule '%s' (id=%s, action=%s) matched.",
            rule.name, rule.id, rule.action,
        )
        return RuleMatch(rule)

    return None


# ─────────────────────────────────────────────────────────────
# LOG WRITER
# ─────────────────────────────────────────────────────────────

def log_routing(
    db: Session,
    *,
    organization_id:  Optional[uuid.UUID] = None,
    user_id:          Optional[uuid.UUID] = None,
    conversation_id:  Optional[uuid.UUID] = None,
    usage_log_id:     Optional[uuid.UUID] = None,
    session_id:       Optional[str]       = None,
    prompt_preview:   Optional[str]       = None,
    prompt_length:    Optional[int]       = None,
    category_slug:    Optional[str]       = None,
    complexity:       Optional[str]       = None,
    complexity_score: Optional[int]       = None,
    department:       Optional[str]       = None,
    router_engine:    Optional[str]       = None,
    rule_id:          Optional[uuid.UUID] = None,
    selected_provider:str                 = "",
    selected_model:   Optional[str]       = None,
    was_fallback:     bool                = False,
    fallback_reason:  Optional[str]       = None,
    original_provider:Optional[str]       = None,
    original_model:   Optional[str]       = None,
    input_tokens:     Optional[int]       = None,
    output_tokens:    Optional[int]       = None,
    total_tokens:     Optional[int]       = None,
    cost_usd:         Optional[float]     = None,
    latency_ms:       Optional[int]       = None,
    status:           Optional[str]       = None,
    error_message:    Optional[str]       = None,
) -> RoutingLog:
    """Persist a routing decision record. Always commits."""
    entry = RoutingLog(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        conversation_id=conversation_id,
        usage_log_id=usage_log_id,
        session_id=session_id,
        prompt_preview=(prompt_preview or "")[:500],
        prompt_length=prompt_length,
        category_slug=category_slug,
        complexity=complexity,
        complexity_score=complexity_score,
        department=department,
        router_engine=router_engine,
        rule_id=rule_id,
        selected_provider=selected_provider,
        selected_model=selected_model,
        was_fallback=was_fallback,
        fallback_reason=fallback_reason,
        original_provider=original_provider,
        original_model=original_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
        status=status or "success",
        error_message=error_message,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# ─────────────────────────────────────────────────────────────
# ANALYTICS
# ─────────────────────────────────────────────────────────────

def get_routing_analytics(
    db: Session,
    *,
    organization_id: Optional[uuid.UUID] = None,
    days: int = 30,
) -> dict:
    """
    Aggregate routing stats over the last `days` calendar days.
    Returns a dict matching RoutingAnalyticsSummary schema.
    """
    now   = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    q = db.query(RoutingLog).filter(RoutingLog.created_at >= since)
    if organization_id:
        q = q.filter(RoutingLog.organization_id == organization_id)

    rows = q.all()

    total_requests  = len(rows)
    total_tokens    = sum(r.total_tokens  or 0 for r in rows)
    total_cost      = float(sum(float(r.cost_usd or 0) for r in rows))
    fallback_count  = sum(1 for r in rows if r.was_fallback)
    error_count     = sum(1 for r in rows if r.status == "error")
    latencies       = [r.latency_ms for r in rows if r.latency_ms is not None]

    avg_cost_usd    = (total_cost / total_requests)  if total_requests else 0.0
    fallback_pct    = (fallback_count / total_requests * 100) if total_requests else 0.0
    error_pct       = (error_count    / total_requests * 100) if total_requests else 0.0
    avg_latency_ms  = (sum(latencies) / len(latencies))       if latencies     else None

    # Per-provider breakdown
    provider_map: dict[str, dict] = {}
    for r in rows:
        p = r.selected_provider or "unknown"
        s = provider_map.setdefault(p, {
            "provider": p,
            "request_count": 0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "latency_sum": 0.0,
            "latency_count": 0,
            "fallback_count": 0,
        })
        s["request_count"]  += 1
        s["total_tokens"]   += r.total_tokens or 0
        s["total_cost_usd"] += float(r.cost_usd or 0)
        if r.latency_ms is not None:
            s["latency_sum"]   += r.latency_ms
            s["latency_count"] += 1
        if r.was_fallback:
            s["fallback_count"] += 1

    providers = []
    for p_data in provider_map.values():
        rc = p_data["request_count"]
        avg_lat = (p_data["latency_sum"] / p_data["latency_count"]) if p_data["latency_count"] else None
        providers.append({
            "provider":       p_data["provider"],
            "request_count":  rc,
            "total_tokens":   p_data["total_tokens"],
            "total_cost_usd": round(p_data["total_cost_usd"], 6),
            "avg_latency_ms": round(avg_lat, 1) if avg_lat else None,
            "fallback_count": p_data["fallback_count"],
            "fallback_pct":   round(p_data["fallback_count"] / rc * 100, 1) if rc else 0.0,
        })
    providers.sort(key=lambda x: x["request_count"], reverse=True)

    # Category counts
    cat_map: dict[str, int] = {}
    for r in rows:
        slug = r.category_slug or "general"
        cat_map[slug] = cat_map.get(slug, 0) + 1
    top_categories = sorted(
        [{"slug": k, "count": v} for k, v in cat_map.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:10]

    return {
        "period_start":    since,
        "period_end":      now,
        "total_requests":  total_requests,
        "total_tokens":    total_tokens,
        "total_cost_usd":  round(total_cost, 6),
        "avg_cost_usd":    round(avg_cost_usd, 6),
        "avg_latency_ms":  round(avg_latency_ms, 1) if avg_latency_ms else None,
        "fallback_count":  fallback_count,
        "fallback_pct":    round(fallback_pct, 2),
        "providers":       providers,
        "top_categories":  top_categories,
        "error_count":     error_count,
        "error_pct":       round(error_pct, 2),
    }
