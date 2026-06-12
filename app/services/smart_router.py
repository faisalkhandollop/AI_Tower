"""
services/smart_router.py - Enterprise Smart Routing

Handles:
- Cost-aware routing (cheapest capable model)
- Department-based routing (Engineering → Claude, HR → free, Marketing → GPT)
- Budget-aware routing (team budget 80%+ → shift to free)
- Smart escalation (poor response → retry on better model)
- Provider priority order (admin-configured)
"""

import os
from typing import Optional

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.models import Department, Team, UserTeam, UsageLog, ProviderPriority

# ── Provider cost tiers ────────────────────────────────────────────────────────
PROVIDER_COST_RANK = {
    "groq":       0,   # free
    "openrouter": 1,   # low
    "gemini":     2,   # medium
    "openai":     3,   # medium-high
    "claude":     4,   # high
}

COMPLEXITY_TO_MIN_TIER = {
    "low":    0,   # any model OK
    "medium": 1,   # at least low-cost
    "high":   3,   # needs quality model
}

# Department routing presets
DEPT_ROUTING_PRESETS = {
    "engineering": {
        "preferred_provider": "claude",
        "fallback_order":     ["openai", "groq"],
        "policy":             "quality_first",
    },
    "hr": {
        "preferred_provider": "groq",
        "fallback_order":     ["openrouter", "gemini"],
        "policy":             "free_first",
    },
    "marketing": {
        "preferred_provider": "openai",
        "fallback_order":     ["groq", "openrouter"],
        "policy":             "cost_aware",
    },
    "finance": {
        "preferred_provider": "claude",
        "fallback_order":     ["openai", "groq"],
        "policy":             "quality_first",
    },
    "sales": {
        "preferred_provider": "openai",
        "fallback_order":     ["groq", "gemini"],
        "policy":             "cost_aware",
    },
    "support": {
        "preferred_provider": "groq",
        "fallback_order":     ["openrouter", "openai"],
        "policy":             "free_first",
    },
}

MODEL_FOR_PROVIDER = {
    "groq":       "llama-3.3-70b-versatile",
    "openrouter": "mistralai/mistral-7b-instruct",
    "openai":     "gpt-4o-mini",
    "claude":     "claude-haiku-4-5-20251001",
    "gemini":     "gemini-pro",
}


def _get_team_budget_pct(team_id: str, db: Session) -> float:
    """Returns what % of the team's budget has been consumed."""
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team or not team.budget_usd:
        return 0.0
    mids = [str(m.user_id) for m in db.query(UserTeam).filter(UserTeam.team_id == team_id).all()]
    if not mids:
        return 0.0
    cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0))\
        .filter(UsageLog.user_id.in_(mids)).scalar() or 0
    return float(cost) / team.budget_usd


def _get_team_quota_pct(team_id: str, db: Session) -> float:
    team = db.query(Team).filter(Team.id == team_id).first()
    if not team or not team.token_quota:
        return 0.0
    mids = [str(m.user_id) for m in db.query(UserTeam).filter(UserTeam.team_id == team_id).all()]
    if not mids:
        return 0.0
    used = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
        .filter(UsageLog.user_id.in_(mids)).scalar() or 0
    return int(used) / team.token_quota


def _admin_provider_order(db: Session) -> list[str]:
    rows = db.query(ProviderPriority)\
        .filter(ProviderPriority.is_enabled == True)\
        .order_by(ProviderPriority.priority).all()
    if rows:
        return [r.provider for r in rows]
    return ["groq", "openrouter", "gemini", "openai", "claude"]


def resolve_provider(
    complexity: str,
    department: Optional[str],
    user_id: Optional[str],
    db: Session,
    requested_provider: Optional[str] = None,
) -> dict:
    """
    Main routing resolver. Returns:
    {
        provider: str,
        model: str,
        reason: str,
        policy_applied: str,
    }
    """
    # 1. Explicit override — trust the caller
    if requested_provider:
        return {
            "provider":       requested_provider,
            "model":          MODEL_FOR_PROVIDER.get(requested_provider, "llama-3.3-70b-versatile"),
            "reason":         "explicit provider override",
            "policy_applied": "override",
        }

    # 2. Get team context for budget/quota
    team_id = None
    budget_pct = 0.0
    quota_pct  = 0.0
    if user_id:
        ut = db.query(UserTeam).filter(UserTeam.user_id == user_id).first()
        if ut:
            team_id    = str(ut.team_id)
            budget_pct = _get_team_budget_pct(team_id, db)
            quota_pct  = _get_team_quota_pct(team_id, db)

    # 3. Budget-aware: if team is >80% budget, force free
    if budget_pct >= 0.80 or quota_pct >= 0.80:
        return {
            "provider":       "groq",
            "model":          MODEL_FOR_PROVIDER["groq"],
            "reason":         f"Budget {round(budget_pct*100,0)}% / Quota {round(quota_pct*100,0)}% — shifted to free model",
            "policy_applied": "budget_aware",
        }

    # 4. Department-based routing
    dept_key = (department or "").lower().strip()
    dept_preset = DEPT_ROUTING_PRESETS.get(dept_key)

    # Also check DB department config
    dept_db = None
    if department:
        dept_db = db.query(Department).filter(
            func.lower(Department.name) == dept_key
        ).first()

    if dept_db and dept_db.routing_policy:
        policy   = dept_db.routing_policy
        pref     = dept_db.preferred_provider
        allowed  = dept_db.allowed_providers or list(MODEL_FOR_PROVIDER.keys())

        if policy == "free_first":
            for p in ["groq", "openrouter"]:
                if p in allowed:
                    return {"provider": p, "model": MODEL_FOR_PROVIDER[p],
                            "reason": f"Dept '{department}' policy: free_first",
                            "policy_applied": "department_free_first"}
        elif policy == "quality_first" and pref:
            return {"provider": pref, "model": MODEL_FOR_PROVIDER.get(pref, MODEL_FOR_PROVIDER["claude"]),
                    "reason": f"Dept '{department}' policy: quality_first → {pref}",
                    "policy_applied": "department_quality_first"}
        elif policy == "department_preferred" and pref:
            if complexity == "high" or pref in allowed:
                return {"provider": pref, "model": MODEL_FOR_PROVIDER.get(pref, MODEL_FOR_PROVIDER["groq"]),
                        "reason": f"Dept '{department}' preferred provider: {pref}",
                        "policy_applied": "department_preferred"}

    elif dept_preset:
        policy = dept_preset["policy"]
        if policy == "free_first":
            return {"provider": dept_preset["preferred_provider"],
                    "model": MODEL_FOR_PROVIDER[dept_preset["preferred_provider"]],
                    "reason": f"Dept '{department}' preset: free_first",
                    "policy_applied": "dept_preset_free_first"}
        elif policy == "quality_first":
            provider = dept_preset["preferred_provider"] if complexity in ("medium", "high") else dept_preset["fallback_order"][0]
            return {"provider": provider, "model": MODEL_FOR_PROVIDER[provider],
                    "reason": f"Dept '{department}' preset: quality_first",
                    "policy_applied": "dept_preset_quality_first"}
        elif policy == "cost_aware":
            if complexity == "low":
                return {"provider": dept_preset["fallback_order"][0],
                        "model": MODEL_FOR_PROVIDER[dept_preset["fallback_order"][0]],
                        "reason": f"Dept '{department}' preset: cost_aware (simple task → free)",
                        "policy_applied": "dept_preset_cost_aware"}
            else:
                return {"provider": dept_preset["preferred_provider"],
                        "model": MODEL_FOR_PROVIDER[dept_preset["preferred_provider"]],
                        "reason": f"Dept '{department}' preset: cost_aware (complex task → preferred)",
                        "policy_applied": "dept_preset_cost_aware"}

    # 5. Cost-aware routing: cheapest model capable of the task
    admin_order = _admin_provider_order(db)
    min_tier = COMPLEXITY_TO_MIN_TIER.get(complexity, 1)

    for provider in admin_order:
        tier = PROVIDER_COST_RANK.get(provider, 5)
        if tier >= min_tier:
            return {
                "provider":       provider,
                "model":          MODEL_FOR_PROVIDER.get(provider, "llama-3.3-70b-versatile"),
                "reason":         f"Cost-aware: cheapest model for complexity='{complexity}'",
                "policy_applied": "cost_aware",
            }

    # 6. Final fallback
    return {
        "provider":       "groq",
        "model":          MODEL_FOR_PROVIDER["groq"],
        "reason":         "Default fallback",
        "policy_applied": "fallback",
    }


def should_escalate(response_text: str, provider: str) -> tuple[bool, str]:
    """
    Detect poor/failed responses and decide whether to escalate.
    Returns (should_escalate: bool, reason: str)
    """
    if not response_text or len(response_text.strip()) < 20:
        return True, "Response too short / empty"

    low_quality_signals = [
        "i cannot", "i can't", "i am unable", "i'm unable",
        "as an ai, i", "i don't have access", "i apologize",
        "error:", "exception:", "timeout",
    ]
    text_lower = response_text.lower()
    for signal in low_quality_signals:
        if signal in text_lower and len(response_text) < 200:
            return True, f"Low-quality signal detected: '{signal}'"

    return False, ""


ESCALATION_CHAIN = {
    "groq":       "openrouter",
    "openrouter": "openai",
    "openai":     "claude",
    "gemini":     "claude",
    "claude":     None,   # already top tier
}

def get_escalation_provider(current_provider: str) -> Optional[str]:
    return ESCALATION_CHAIN.get(current_provider)
