"""
api/usage.py - Usage logging and retrieval endpoints.

Routes:
    POST /usage/log              → log a request (gateway calls this)
    GET  /usage/logs             → paginated log list (admin only)
    GET  /usage/logs/{log_id}    → single log (admin only)
    GET  /usage/report           → aggregate report (admin only)
    GET  /usage/history          → current user's chat history
    GET  /usage/history/{log_id} → single exchange by id (owner or admin)
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import UsageLog, Model, User
from app.schemas.usage import UsageLogCreate, UsageReportResponse
from app.services.auth import get_current_user, require_admin
from app.services.cost import calculate_cost

router = APIRouter(prefix="/usage", tags=["Usage"])

MAX_PROMPT_BYTES  = 50_000   # 50 KB hard cap on stored prompt
MAX_RESPONSE_BYTES = 100_000 # 100 KB hard cap on stored response


# ── POST /usage/log ────────────────────────────────────────────────────────────
@router.post("/log", summary="Log an AI request (called by gateway)")
def log_usage_route(
    payload: UsageLogCreate,
    db: Session = Depends(get_db),
):
    """
    Internal endpoint called by Lokesh's gateway after every AI response.
    No auth required — internal network call only.
    Input sizes are capped to prevent storage abuse.
    """
    # Enforce size caps
    prompt_text   = payload.prompt[:MAX_PROMPT_BYTES]
    response_text = payload.response[:MAX_RESPONSE_BYTES]

    # Resolve model pricing
    model = db.query(Model).filter(
        Model.model_name == payload.model
    ).first()

    if model:
        cost_data = calculate_cost(
            input_tokens=payload.input_tokens,
            output_tokens=payload.output_tokens,
            input_cost_per_1k=float(model.input_cost_per_1k),
            output_cost_per_1k=float(model.output_cost_per_1k),
        )
    else:
        cost_data = {"input_cost": 0, "output_cost": 0, "total_cost": 0}

    total_tokens = payload.input_tokens + payload.output_tokens

    parsed_user_id = None
    if payload.user_id:
        try:
            parsed_user_id = uuid.UUID(payload.user_id)
        except ValueError:
            parsed_user_id = None

    # Quota enforcement — reject if user has exceeded their token quota
    if parsed_user_id:
        user = db.query(User).filter(User.id == parsed_user_id).first()
        if user:
            from sqlalchemy import func as sqlfunc
            used = db.query(
                sqlfunc.coalesce(sqlfunc.sum(UsageLog.total_tokens), 0)
            ).filter(UsageLog.user_id == parsed_user_id).scalar() or 0

            if used >= user.token_quota:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"Token quota exceeded. "
                        f"Used: {used} / Limit: {user.token_quota}. "
                        f"Contact your administrator to increase your quota."
                    ),
                )

    usage = UsageLog(
        user_id       = parsed_user_id,
        session_id    = payload.session_id,
        prompt        = prompt_text,
        response      = response_text,
        provider      = payload.provider,
        model         = payload.model,
        complexity    = payload.complexity,
        input_tokens  = payload.input_tokens,
        output_tokens = payload.output_tokens,
        total_tokens  = total_tokens,
        cost          = cost_data["total_cost"],
    )

    db.add(usage)
    db.commit()
    db.refresh(usage)

    return {
        "id":            str(usage.id),
        "prompt":        prompt_text,
        "complexity":    payload.complexity,
        "provider":      payload.provider,
        "model":         payload.model,
        "response":      response_text,
        "input_tokens":  payload.input_tokens,
        "output_tokens": payload.output_tokens,
        "cost":          cost_data["total_cost"],
        "user_id":       str(parsed_user_id) if parsed_user_id else None,
        "session_id":    payload.session_id,
    }


# ── GET /usage/logs ────────────────────────────────────────────────────────────
@router.get("/logs", summary="All usage logs, paginated (admin only)")
def get_all_logs(
    page:  int = Query(1, ge=1, description="Page number (1-based)"),
    limit: int = Query(50, ge=1, le=200, description="Records per page"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Returns paginated usage logs, newest first. Max 200 per page."""
    offset = (page - 1) * limit
    total  = db.query(UsageLog).count()
    logs   = (
        db.query(UsageLog)
        .order_by(UsageLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "logs": [
            {
                "id":            str(log.id),
                "user_id":       str(log.user_id) if log.user_id else None,
                "session_id":    log.session_id,
                "prompt":        log.prompt,
                "response":      log.response,
                "provider":      log.provider,
                "model":         log.model,
                "complexity":    log.complexity,
                "input_tokens":  log.input_tokens,
                "output_tokens": log.output_tokens,
                "total_tokens":  log.total_tokens,
                "cost":          float(log.cost),
                "created_at":    str(log.created_at),
            }
            for log in logs
        ],
    }


# ── GET /usage/logs/{log_id} ───────────────────────────────────────────────────
@router.get("/logs/{log_id}", summary="Get a single log entry (admin only)")
def get_log_by_id(
    log_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    log = db.query(UsageLog).filter(UsageLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail=f"Log '{log_id}' not found")

    return {
        "id":            str(log.id),
        "user_id":       str(log.user_id) if log.user_id else None,
        "session_id":    log.session_id,
        "prompt":        log.prompt,
        "response":      log.response,
        "provider":      log.provider,
        "model":         log.model,
        "complexity":    log.complexity,
        "input_tokens":  log.input_tokens,
        "output_tokens": log.output_tokens,
        "total_tokens":  log.total_tokens,
        "cost":          float(log.cost),
        "created_at":    str(log.created_at),
    }


# ── GET /usage/report ──────────────────────────────────────────────────────────
@router.get(
    "/report",
    response_model=UsageReportResponse,
    summary="Aggregate usage report (admin only)",
)
def usage_report(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """SQL-aggregated report — safe at any row count."""
    from sqlalchemy import func as sqlfunc
    result = db.query(
        sqlfunc.count(UsageLog.id)                              .label("total_requests"),
        sqlfunc.coalesce(sqlfunc.sum(UsageLog.input_tokens), 0) .label("total_input_tokens"),
        sqlfunc.coalesce(sqlfunc.sum(UsageLog.output_tokens),0) .label("total_output_tokens"),
        sqlfunc.coalesce(sqlfunc.sum(UsageLog.cost),         0) .label("total_cost"),
    ).one()

    return {
        "total_requests":      result.total_requests,
        "total_input_tokens":  int(result.total_input_tokens),
        "total_output_tokens": int(result.total_output_tokens),
        "total_cost":          round(float(result.total_cost), 6),
    }


# ── GET /usage/history ─────────────────────────────────────────────────────────
@router.get("/history", summary="Current user's chat history")
def get_my_history(
    page:  int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns the authenticated user's own message history, paginated.
    Each item represents one prompt/response exchange.
    """
    offset = (page - 1) * limit
    total  = db.query(UsageLog).filter(UsageLog.user_id == current_user.id).count()
    logs   = (
        db.query(UsageLog)
        .filter(UsageLog.user_id == current_user.id)
        .order_by(UsageLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total": total,
        "page":  page,
        "limit": limit,
        "pages": (total + limit - 1) // limit,
        "history": [
            {
                "id":            str(log.id),
                "session_id":    log.session_id,
                "prompt":        log.prompt,
                "response":      log.response,
                "provider":      log.provider,
                "model":         log.model,
                "complexity":    log.complexity,
                "input_tokens":  log.input_tokens,
                "output_tokens": log.output_tokens,
                "total_tokens":  log.total_tokens,
                "cost":          float(log.cost),
                "created_at":    str(log.created_at),
            }
            for log in logs
        ],
    }


# ── GET /usage/history/{log_id} ────────────────────────────────────────────────
@router.get("/history/{log_id}", summary="Single exchange by ID")
def get_history_by_id(
    log_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Returns a single prompt/response exchange.
    Users can only see their own logs; admins can see any log.
    """
    log = db.query(UsageLog).filter(UsageLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail=f"Log '{log_id}' not found")

    # Access control: owner or admin
    is_owner = log.user_id == current_user.id
    is_admin = current_user.role == "admin"
    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "id":            str(log.id),
        "session_id":    log.session_id,
        "prompt":        log.prompt,
        "response":      log.response,
        "provider":      log.provider,
        "model":         log.model,
        "complexity":    log.complexity,
        "input_tokens":  log.input_tokens,
        "output_tokens": log.output_tokens,
        "total_tokens":  log.total_tokens,
        "cost":          float(log.cost),
        "created_at":    str(log.created_at),
    }
