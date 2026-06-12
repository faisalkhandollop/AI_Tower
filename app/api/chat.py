"""
api/chat.py - AI Gateway (upgraded with smart enterprise routing)

POST /chat/        → send prompt with smart routing (cost/dept/budget aware)
GET  /chat/health  → provider health check
"""

import asyncio
import os
from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field, field_validator
from typing import Optional
from sqlalchemy.orm import Session

from app.providers import claude, gemini, groq, openai, openrouter
from app.router.router import PromptRouter
from app.router.conversation_context import ConversationContextManager
from app.router.smart_model_selector import SmartModelSelectorV3
from app.services.smart_router import resolve_provider, should_escalate, get_escalation_provider, MODEL_FOR_PROVIDER
from app.db.database import get_db

router = APIRouter(prefix="/chat", tags=["AI Chat (Gateway)"])

MODEL_TRANSLATION = {
    "llama-3-8b": ("groq", "llama-3.3-70b-versatile"),
    "llama-3-70b": ("groq", "llama-3.3-70b-versatile"),
    "llama-3.1-8b": ("groq", "llama-3.3-70b-versatile"),
    "gemma-3": ("groq", "llama-3.3-70b-versatile"),
    "mistral-7b": ("openrouter", "mistralai/mistral-7b-instruct"),
    "deepseek-r1": ("openrouter", "mistralai/mistral-7b-instruct"),
    "gpt-4o": ("claude", "claude-haiku-4-5-20251001"),
    "gpt-4o-mini": ("claude", "claude-haiku-4-5-20251001"),
    "claude-opus": ("claude", "claude-haiku-4-5-20251001"),
    "claude-sonnet-4": ("claude", "claude-haiku-4-5-20251001"),
    "gemini-pro": ("gemini", "gemini-pro"),
    "gemini-1.5-pro": ("gemini", "gemini-1.5-pro"),
}

FALLBACK_CHAIN = [
    {"provider": "groq",       "model": "llama-3.3-70b-versatile"},
    {"provider": "openrouter", "model": "mistralai/mistral-7b-instruct"},
    {"provider": "claude",     "model": "claude-haiku-4-5-20251001"},
]

PROVIDER_MAP = {
    "groq": groq, "openrouter": openrouter,
    "openai": openai, "claude": claude, "gemini": gemini,
}

_health_cache: dict[str, bool] = {p: True for p in PROVIDER_MAP}
_manual_router   = PromptRouter()
_context_manager = ConversationContextManager()
_model_selector  = SmartModelSelectorV3()


class ChatRequest(BaseModel):
    prompt:     str            = Field(..., min_length=1, max_length=10_000)
    provider:   Optional[str]  = None
    model:      Optional[str]  = None
    user_id:    Optional[str]  = None
    session_id: Optional[str]  = None
    department: Optional[str]  = None   # NEW: department-based routing
    team_id:    Optional[str]  = None   # NEW: team budget-aware routing

    @field_validator("prompt")
    @classmethod
    def strip_prompt(cls, v):
        v = v.strip()
        if not v: raise ValueError("Prompt must not be empty")
        return v

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v):
        if v is None: return v
        allowed = {"groq", "claude", "openai", "openrouter", "gemini"}
        if v.lower() not in allowed:
            raise ValueError(f"provider must be one of: {', '.join(sorted(allowed))}")
        return v.lower()


class ChatResponse(BaseModel):
    response:       str
    provider:       str
    model:          str
    complexity:     str
    routing_reason: Optional[str] = None
    policy_applied: Optional[str] = None
    session_id:     Optional[str] = None
    escalated:      bool          = False
    tokens:         dict          = Field(default_factory=dict)


def _get_routing_direct(prompt: str, session_id: Optional[str] = None) -> dict:
    session = _context_manager.get_or_create_session(session_id)
    context = _context_manager.analyze_context(prompt=prompt, session=session)
    try:
        route_result = _manual_router.route(context.enriched_prompt)
        complexity_score = route_result.complexity_score
        category = route_result.category
        reasons  = route_result.reasons
    except Exception:
        complexity_score, category, reasons = 30, "simple", ["fallback"]

    model_result = _model_selector.select(complexity_score=complexity_score, context=context)
    _context_manager.add_to_history(
        session=session, role="user", content=prompt,
        complexity_score=complexity_score,
        recommended_model=model_result.recommended_model,
    )
    complexity_map = {"simple": "low", "medium": "medium", "complex": "high"}
    return {
        "complexity":        complexity_map.get(category, "medium"),
        "recommended_model": model_result.recommended_model,
        "reason":            reasons[0] if reasons else "routing",
        "session_id":        session.session_id,
    }


async def _call_provider(provider: str, model: str, prompt: str) -> dict:
    module = PROVIDER_MAP.get(provider)
    if not module:
        raise Exception(f"Unknown provider: {provider}")
    try:
        result = await module.chat(model=model, prompt=prompt)
        _health_cache[provider] = True
        return result
    except Exception as e:
        _health_cache[provider] = False
        raise Exception(f"{provider} failed: {e}")


async def _log_usage_direct(payload: dict) -> None:
    try:
        import uuid as _uuid
        from app.db.database import SessionLocal
        from app.db.models import UsageLog, Model, User
        from app.services.cost import calculate_cost
        from sqlalchemy import func as sqlfunc

        db = SessionLocal()
        try:
            model_obj = db.query(Model).filter(Model.model_name == payload.get("model", "")).first()
            if model_obj:
                cd = calculate_cost(
                    payload.get("input_tokens", 0), payload.get("output_tokens", 0),
                    float(model_obj.input_cost_per_1k), float(model_obj.output_cost_per_1k),
                )
            else:
                cd = {"total_cost": 0}

            uid = None
            if payload.get("user_id"):
                try: uid = _uuid.UUID(payload["user_id"])
                except ValueError: pass

            if uid:
                user = db.query(User).filter(User.id == uid).first()
                if user:
                    used = db.query(sqlfunc.coalesce(sqlfunc.sum(UsageLog.total_tokens), 0))\
                        .filter(UsageLog.user_id == uid).scalar() or 0
                    if used >= user.token_quota:
                        return

            db.add(UsageLog(
                user_id=uid, session_id=payload.get("session_id"),
                prompt=payload.get("prompt", "")[:50_000],
                response=payload.get("response", "")[:100_000],
                provider=payload.get("provider", ""), model=payload.get("model", ""),
                complexity=payload.get("complexity", "medium"),
                input_tokens=payload.get("input_tokens", 0),
                output_tokens=payload.get("output_tokens", 0),
                total_tokens=payload.get("input_tokens", 0) + payload.get("output_tokens", 0),
                cost=cd["total_cost"],
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[Logger] {e}")


@router.post("/", response_model=ChatResponse, summary="Send a prompt to AI (smart routed)")
async def chat(payload: ChatRequest, db: Session = Depends(get_db)):
    """
    Intelligent AI chat with:
    - Cost-aware routing (cheapest capable model)
    - Department-based routing (Engineering→Claude, HR→free)
    - Budget-aware routing (team >80% budget → free models)
    - Smart escalation (poor response → retry on better model)
    - Admin-configured provider priority
    """
    try:
        # Step 1: Get complexity from AI Router
        routing      = _get_routing_direct(payload.prompt, session_id=payload.session_id)
        complexity   = routing.get("complexity", "medium")
        active_session = routing.get("session_id", payload.session_id)

        # Step 2: Resolve provider via enterprise smart router
        smart = resolve_provider(
            complexity=complexity,
            department=payload.department,
            user_id=payload.user_id,
            db=db,
            requested_provider=payload.provider,
        )
        real_provider   = smart["provider"]
        real_model      = MODEL_FOR_PROVIDER.get(real_provider, "llama-3.3-70b-versatile")
        routing_reason  = smart["reason"]
        policy_applied  = smart["policy_applied"]

        # Override model if translated
        if payload.model and payload.model in MODEL_TRANSLATION:
            real_provider, real_model = MODEL_TRANSLATION[payload.model]

        # Step 3: Call provider with escalation support
        result    = None
        escalated = False
        errors    = []

        for attempt in range(3):
            try:
                result = await _call_provider(real_provider, real_model, payload.prompt)
                # Check response quality — escalate if poor
                if attempt == 0:
                    do_escalate, esc_reason = should_escalate(result.get("response", ""), real_provider)
                    if do_escalate:
                        next_provider = get_escalation_provider(real_provider)
                        if next_provider:
                            errors.append(f"Escalating from {real_provider}: {esc_reason}")
                            real_provider = next_provider
                            real_model    = MODEL_FOR_PROVIDER[real_provider]
                            escalated     = True
                            result        = None
                            continue
                break
            except Exception as e:
                errors.append(str(e))
                for fb in FALLBACK_CHAIN:
                    if fb["provider"] == real_provider:
                        continue
                    if not _health_cache.get(fb["provider"], True):
                        continue
                    try:
                        result = await _call_provider(fb["provider"], fb["model"], payload.prompt)
                        real_provider = fb["provider"]
                        real_model    = fb["model"]
                        break
                    except Exception as fe:
                        errors.append(str(fe))
                if result:
                    break

        if result is None:
            raise Exception(f"All providers failed: {' | '.join(errors)}")

        # Step 4: Log usage
        tokens = result.get("tokens", {})
        loop   = asyncio.get_running_loop()
        loop.create_task(_log_usage_direct({
            "prompt": payload.prompt, "response": result.get("response", ""),
            "provider": result.get("provider", real_provider),
            "model": result.get("model", real_model),
            "complexity": {"low": "low", "medium": "medium", "high": "high"}.get(complexity, "medium"),
            "input_tokens": tokens.get("prompt_tokens", 0),
            "output_tokens": tokens.get("completion_tokens", 0),
            "user_id": payload.user_id, "session_id": active_session,
        }))

        return ChatResponse(
            response=result.get("response", ""),
            provider=result.get("provider", real_provider),
            model=result.get("model", real_model),
            complexity=complexity,
            routing_reason=routing_reason,
            policy_applied=policy_applied,
            session_id=active_session,
            escalated=escalated,
            tokens=tokens,
        )

    except Exception as e:
        err_msg = str(e)
        if "quota" in err_msg.lower():
            raise HTTPException(status_code=429, detail=err_msg)
        raise HTTPException(status_code=503, detail=f"All AI providers failed: {err_msg}")


@router.get("/health", summary="Provider health status")
async def chat_health():
    async def check(name, module):
        try:
            ok = await asyncio.wait_for(module.health_check(), timeout=8)
            return name, ok
        except Exception:
            return name, False

    results = await asyncio.gather(*[check(n, m) for n, m in PROVIDER_MAP.items()])
    status_map = {name: ("healthy" if ok else "unhealthy") for name, ok in results}
    for name, ok in results:
        _health_cache[name] = ok
    overall = "healthy" if any(ok for _, ok in results) else "degraded"
    return {"status": overall, "providers": status_map,
            "fallback_chain": [f["provider"] for f in FALLBACK_CHAIN]}
