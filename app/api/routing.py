"""
app/api/routing.py - All AI Router endpoints (merged from ai_router/app.py).

Routes:
    POST /route                → Manual scoring engine (fast, offline)
    POST /route/simple         → Manual scoring — simple 3-field output
    POST /route/file           → File-aware routing (AI + manual fallback)
    POST /route/batch          → Multiple prompts at once
    POST /compare              → Manual vs AI comparison

    POST /ai/route             → Ollama AI routing (smart)
    POST /ai/route/simple      → Ollama AI — simple 3-field output

    GET  /models               → Available free + premium models list
    GET  /stats                → Usage statistics

    POST /workspace/route      → Context-aware routing with session
    GET  /workspace/suggestions → Smart prompt suggestions
    GET  /workspace/session/{id} → Session info + token usage
"""

import logging
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from app.router.config import settings, FREE_MODELS, PREMIUM_MODELS
from app.router.models import (
    AIRouteResponse,
    FileRouteResponse,
    HealthResponse,
    RouteRequest,
    RouteResponse,
    SimpleRouteResponse,
    WelcomeResponse,
)
from app.router.ollama_service import OllamaService
from app.router.router import PromptRouter
from app.router.services.file_routing_service import FileRoutingService
from app.router.conversation_context import ConversationContextManager
from app.router.smart_model_selector import SmartModelSelectorV3
from app.router.suggested_prompts import SuggestedPromptsEngine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["AI Router"])

# ── Singletons ─────────────────────────────────────────────────────────────────
_manual_router = PromptRouter()
_file_routing_service = FileRoutingService()
_context_manager = ConversationContextManager()
_model_selector_v3 = SmartModelSelectorV3()
_suggestions_engine = SuggestedPromptsEngine()

# ── In-memory stats ────────────────────────────────────────────────────────────
_stats: dict = {
    "total_requests": 0,
    "route_hits": defaultdict(int),
    "model_hits": defaultdict(int),
    "total_complexity_score": 0,
    "started_at": time.time(),
}


def _track(endpoint: str, model: str = "", score: int = 0) -> None:
    _stats["total_requests"] += 1
    _stats["route_hits"][endpoint] += 1
    if model:
        _stats["model_hits"][model] += 1
    if score:
        _stats["total_complexity_score"] += score


def get_ollama_service() -> OllamaService:
    return OllamaService()


# ══════════════════════════════════════════════════════════════════════════════
# GENERAL
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/models", summary="List all available models", tags=["AI Router"])
async def list_models() -> dict:
    """Returns all available free and premium models."""
    return {
        "free_models": list(FREE_MODELS),
        "premium_models": list(PREMIUM_MODELS),
        "total_free": len(FREE_MODELS),
        "total_premium": len(PREMIUM_MODELS),
        "ollama_model": settings.OLLAMA_MODEL,
        "routing_rules": {
            "score_0_30": "simple → free",
            "score_31_70": "medium → free",
            "score_71_100": "complex → premium",
        },
    }


@router.get("/stats", summary="Usage statistics", tags=["AI Router"])
async def get_stats() -> dict:
    """Returns API usage statistics."""
    total = _stats["total_requests"]
    avg_score = (
        round(_stats["total_complexity_score"] / total, 1)
        if total > 0 else 0
    )
    uptime_seconds = int(time.time() - _stats["started_at"])
    uptime_str = f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m"

    return {
        "total_requests": total,
        "uptime": uptime_str,
        "avg_complexity_score": avg_score,
        "requests_per_endpoint": dict(_stats["route_hits"]),
        "model_usage": dict(
            sorted(_stats["model_hits"].items(), key=lambda x: x[1], reverse=True)
        ),
        "most_used_model": (
            max(_stats["model_hits"], key=_stats["model_hits"].get)
            if _stats["model_hits"] else "none"
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# ROUTING — MANUAL
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/route",
    response_model=RouteResponse,
    summary="Route a prompt (Manual scoring)",
    tags=["Routing — Manual"],
)
async def route_manual(request: RouteRequest) -> RouteResponse:
    """
    **Manual keyword + scoring engine.**
    Fast, works offline, no Ollama needed.
    Returns full detail: score_breakdown + keyword_analysis.
    """
    logger.info("POST /route (manual) — prompt len=%d.", len(request.prompt))
    try:
        result = _manual_router.route(request.prompt)
        _track("/route", result.recommended_model, result.complexity_score)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/route/simple",
    response_model=SimpleRouteResponse,
    summary="Route a prompt — simple output (Manual)",
    tags=["Routing — Manual"],
)
async def route_simple_manual(request: RouteRequest) -> SimpleRouteResponse:
    """**Manual scoring — simplified 3-field output.**"""
    logger.info("POST /route/simple — prompt len=%d.", len(request.prompt))
    try:
        result = _manual_router.route(request.prompt)
        _track("/route/simple", result.recommended_model, result.complexity_score)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    complexity_map = {"simple": "low", "medium": "medium", "complex": "high"}
    reason_map = {
        "simple": "simple content generation",
        "medium": "moderate complexity task",
        "complex": "high complexity — architecture / analysis / large context",
    }
    return SimpleRouteResponse(
        complexity=complexity_map[result.category],
        recommended_model=result.recommended_model,
        reason=reason_map[result.category],
    )


@router.post(
    "/route/batch",
    summary="Route multiple prompts at once",
    tags=["Routing — Manual"],
)
async def route_batch(requests: list[RouteRequest]) -> dict:
    """
    **Batch routing — send multiple prompts in one request.**
    Max 20 prompts per batch.
    """
    if len(requests) > 20:
        raise HTTPException(
            status_code=400,
            detail="Max 20 prompts per batch request."
        )

    logger.info("POST /route/batch — %d prompts.", len(requests))
    results = []

    for i, req in enumerate(requests):
        try:
            result = _manual_router.route(req.prompt)
            _track("/route/batch", result.recommended_model, result.complexity_score)
            results.append({
                "index": i,
                "prompt_preview": req.prompt[:80],
                "complexity_score": result.complexity_score,
                "category": result.category,
                "route": result.route,
                "recommended_model": result.recommended_model,
                "confidence": result.confidence,
            })
        except Exception as e:
            results.append({
                "index": i,
                "prompt_preview": req.prompt[:80],
                "error": str(e),
            })

    return {
        "total": len(requests),
        "results": results,
        "summary": {
            "free": sum(1 for r in results if r.get("route") == "free"),
            "premium": sum(1 for r in results if r.get("route") == "premium"),
            "errors": sum(1 for r in results if "error" in r),
        },
    }


@router.post(
    "/route/file",
    response_model=FileRouteResponse,
    summary="Route a file + prompt (File-aware routing)",
    tags=["Routing — File Aware"],
)
async def route_file(
    file: UploadFile = File(..., description="Upload PDF, DOCX, CSV, or code file"),
    prompt: str = Form(..., description="Describe what you want to do with this file"),
    ollama_service: OllamaService = Depends(get_ollama_service),
) -> FileRouteResponse:
    """
    **File-aware routing — analyzes file + prompt together.**

    Rules:
    - PDF > 50 pages → Claude (premium)
    - CSV any size → GPT (premium)
    - Code files → Claude (premium)
    - DOCX > 30 pages → Claude (premium)
    - Small files → Free model
    """
    logger.info("POST /route/file — file='%s' prompt_len=%d.", file.filename, len(prompt))

    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read file: {exc}") from exc

    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    prompt_result = None
    try:
        prompt_result = ollama_service.route(prompt)
    except Exception as exc:
        logger.warning("AI failed (%s) — manual fallback.", exc)
        try:
            prompt_result = _manual_router.route(prompt)
        except Exception:
            pass

    result = _file_routing_service.route(
        filename=file.filename or "unknown",
        file_bytes=file_bytes,
        prompt=prompt,
        prompt_route_result=prompt_result,
    )

    _track("/route/file", result.recommended_model, result.complexity_score)

    return FileRouteResponse(
        filename=result.filename,
        file_type=result.file_type,
        size_kb=result.size_kb,
        page_count=result.page_count,
        row_count=result.row_count,
        line_count=result.line_count,
        prompt_preview=result.prompt_preview,
        prompt_complexity=result.prompt_complexity,
        complexity_score=result.complexity_score,
        category=result.category,
        route=result.route,
        recommended_model=result.recommended_model,
        confidence=result.confidence,
        reasons=result.reasons,
        routed_by=result.routed_by,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ROUTING — AI POWERED
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/ai/route",
    response_model=AIRouteResponse,
    summary="Route a prompt (Ollama AI)",
    tags=["Routing — AI Powered"],
)
async def route_ai(
    request: RouteRequest,
    ollama_service: OllamaService = Depends(get_ollama_service),
) -> AIRouteResponse:
    """
    **Ollama AI powered routing.**
    Uses deepseek-r1:8b model. Smart context-aware routing.
    """
    logger.info("POST /ai/route — prompt len=%d.", len(request.prompt))
    try:
        result = ollama_service.route(request.prompt)
        _track("/ai/route", result.recommended_model, result.complexity_score)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/ai/route/simple",
    response_model=SimpleRouteResponse,
    summary="Route a prompt — simple output (Ollama AI)",
    tags=["Routing — AI Powered"],
)
async def route_ai_simple(
    request: RouteRequest,
    ollama_service: OllamaService = Depends(get_ollama_service),
) -> SimpleRouteResponse:
    """**Ollama AI routing — simplified 3-field output.**"""
    logger.info("POST /ai/route/simple — prompt len=%d.", len(request.prompt))
    try:
        result = ollama_service.route(request.prompt)
        _track("/ai/route/simple", result.recommended_model, result.complexity_score)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    complexity_map = {"simple": "low", "medium": "medium", "complex": "high"}
    reason_map = {
        "simple": "simple content generation",
        "medium": "moderate complexity task",
        "complex": "high complexity — architecture / analysis / large context",
    }
    return SimpleRouteResponse(
        complexity=complexity_map.get(result.category, "medium"),
        recommended_model=result.recommended_model,
        reason=result.reasons[0] if result.reasons else reason_map.get(result.category, "AI analysis"),
    )


@router.post(
    "/compare",
    summary="Compare Manual vs AI routing for same prompt",
    tags=["Routing — AI Powered"],
)
async def compare_routes(
    request: RouteRequest,
    ollama_service: OllamaService = Depends(get_ollama_service),
) -> dict:
    """**Compare Manual engine vs Ollama AI for the same prompt.**"""
    logger.info("POST /compare — prompt len=%d.", len(request.prompt))
    _track("/compare")

    # Manual result
    manual_result = None
    manual_error = None
    try:
        r = _manual_router.route(request.prompt)
        manual_result = {
            "complexity_score": r.complexity_score,
            "category": r.category,
            "route": r.route,
            "recommended_model": r.recommended_model,
            "confidence": r.confidence,
            "reasons": r.reasons,
            "score_breakdown": r.score_breakdown.model_dump() if r.score_breakdown else None,
        }
    except Exception as e:
        manual_error = str(e)

    # AI result
    ai_result = None
    ai_error = None
    try:
        r = ollama_service.route(request.prompt)
        ai_result = {
            "complexity_score": r.complexity_score,
            "category": r.category,
            "route": r.route,
            "recommended_model": r.recommended_model,
            "confidence": r.confidence,
            "reasons": r.reasons,
        }
    except Exception as e:
        ai_error = str(e)

    agreement = False
    if manual_result and ai_result:
        agreement = manual_result["route"] == ai_result["route"]

    return {
        "prompt_preview": request.prompt[:100],
        "manual_engine": manual_result or {"error": manual_error},
        "ai_engine": ai_result or {"error": ai_error},
        "agreement": agreement,
        "verdict": (
            "✅ Both engines agree"
            if agreement
            else "⚠️ Engines disagree — review manually"
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# WORKSPACE INTELLIGENCE
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/workspace/route",
    summary="Context-aware routing with conversation history",
    tags=["Workspace Intelligence"],
)
async def workspace_route(
    request: RouteRequest,
    session_id: str | None = None,
    ollama_service: OllamaService = Depends(get_ollama_service),
) -> dict:
    """
    **Context-aware routing — uses ALL signals:**
    - Prompt complexity (7-dimension scoring)
    - Chat history (topic detection, followup enrichment)
    - Token budget tracking
    - Smart model selection V3

    Pass `session_id` to maintain conversation continuity.
    """
    logger.info(
        "POST /workspace/route — session=%s prompt_len=%d.",
        session_id, len(request.prompt),
    )

    session = _context_manager.get_or_create_session(session_id)
    context = _context_manager.analyze_context(
        prompt=request.prompt,
        session=session,
    )

    route_result = None
    engine_used = "none"
    try:
        route_result = ollama_service.route(context.enriched_prompt)
        engine_used = "ai"
    except Exception as e:
        logger.warning("Workspace AI failed (%s) — manual fallback.", e)
        try:
            route_result = _manual_router.route(context.enriched_prompt)
            engine_used = "manual"
        except Exception as e2:
            logger.warning("Workspace manual fallback failed: %s", e2)

    complexity_score = getattr(route_result, "complexity_score", 50)
    category = getattr(route_result, "category", "medium")
    reasons = getattr(route_result, "reasons", ["Context-aware routing"])

    model_result = _model_selector_v3.select(
        complexity_score=complexity_score,
        context=context,
    )

    _context_manager.add_to_history(
        session=session,
        role="user",
        content=request.prompt,
        complexity_score=complexity_score,
        recommended_model=model_result.recommended_model,
    )

    _track("/workspace/route", model_result.recommended_model, complexity_score)

    return {
        "session_id": session.session_id,
        "prompt_preview": request.prompt[:120],
        "complexity_score": complexity_score,
        "category": category,
        "route": model_result.route,
        "recommended_model": model_result.recommended_model,
        "confidence": model_result.confidence,
        "reasons": reasons,
        "engine_used": engine_used,
        "context": {
            "session_id": session.session_id,
            "detected_topic": context.detected_topic,
            "is_followup": context.is_followup,
            "context_summary": context.context_summary,
            "previous_complexity": context.previous_complexity,
            "has_file_context": context.has_file_context,
            "recent_file": context.recent_file,
            "estimated_tokens": context.estimated_tokens,
            "token_budget_status": context.token_budget_status,
            "enriched_prompt": context.enriched_prompt,
        },
        "model_selection": {
            "recommended_model": model_result.recommended_model,
            "route": model_result.route,
            "selection_reason": model_result.selection_reason,
            "signals_used": model_result.signals_used,
            "confidence": model_result.confidence,
            "final_score": model_result.final_score,
        },
    }


@router.get(
    "/workspace/suggestions",
    summary="Get smart prompt suggestions based on usage",
    tags=["Workspace Intelligence"],
)
async def workspace_suggestions(
    session_id: str | None = None,
    topic: str = "general",
    file_type: str = "",
) -> dict:
    """
    **Smart prompt suggestions based on:**
    - Current topic (flutter, python, architecture, etc.)
    - Recent file uploads (pdf, csv, code)
    - Time of day
    """
    logger.info(
        "GET /workspace/suggestions — session=%s topic=%s file=%s.",
        session_id, topic, file_type,
    )

    session = _context_manager.get_or_create_session(session_id)
    suggestions = _suggestions_engine.generate(
        session=session,
        current_topic=topic,
        recent_file_type=file_type,
        max_suggestions=6,
    )

    based_on_parts = []
    if topic and topic != "general":
        based_on_parts.append(f"{topic} topic")
    if file_type:
        based_on_parts.append(f"{file_type} file")
    if not based_on_parts:
        based_on_parts.append("default + time-based")

    return {
        "session_id": session.session_id,
        "suggestions": _suggestions_engine.to_response(suggestions),
        "based_on": " | ".join(based_on_parts),
    }


@router.get(
    "/workspace/session/{session_id}",
    summary="Get session info and token usage",
    tags=["Workspace Intelligence"],
)
async def workspace_session_info(session_id: str) -> dict:
    """
    **Get conversation session details:**
    - Message count and history
    - Token usage and budget status
    - Topics discussed
    - Files uploaded
    - Average complexity
    """
    logger.info("GET /workspace/session/%s", session_id)

    session = _context_manager.get_or_create_session(session_id)

    total_tokens = session.total_tokens_used
    if total_tokens >= _context_manager.TOKEN_CRITICAL:
        budget = "critical"
    elif total_tokens >= _context_manager.TOKEN_WARNING:
        budget = "warning"
    else:
        budget = "ok"

    return {
        "session_id": session.session_id,
        "message_count": session.message_count,
        "total_tokens_used": total_tokens,
        "avg_complexity": round(session.avg_complexity, 1),
        "topics_discussed": list(session.topic_counts.keys()),
        "files_uploaded": session.file_uploads,
        "token_budget_status": budget,
    }
