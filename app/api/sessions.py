from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import Session, UsageLog, User
from app.schemas.session import SessionUpsert, SessionResponse, UserSessionsResponse
from app.services.auth import get_current_user, require_admin

router = APIRouter(prefix="/sessions", tags=["Sessions"])


def _build_response(session: Session, db: DBSession) -> SessionResponse:
    agg = (
        db.query(
            func.count(UsageLog.id).label("total_requests"),
            func.coalesce(func.sum(UsageLog.cost), 0).label("total_cost"),
        )
        .filter(UsageLog.session_id == session.session_id)
        .first()
    )

    return SessionResponse(
        session_id          = session.session_id,
        user_id             = str(session.user_id) if session.user_id else None,
        message_count       = session.message_count,
        total_tokens_used   = session.total_tokens_used,
        avg_complexity      = session.avg_complexity,
        token_budget_status = session.token_budget_status,
        topics_discussed    = session.topics_discussed or [],
        files_uploaded      = session.files_uploaded   or [],
        created_at          = session.created_at,
        last_active         = session.last_active,
        total_requests      = agg.total_requests if agg else 0,
        total_cost          = float(agg.total_cost) if agg else 0.0,
    )


# ── POST /sessions/upsert ──────────────────────────────────────────────────────
@router.post("/upsert", response_model=SessionResponse, summary="Create or update a session")
def upsert_session(payload: SessionUpsert, db: DBSession = Depends(get_db)):
    """
    Called by Lokesh's gateway after every /workspace/route call.
    Creates the session on first message, updates it on every subsequent one.
    No auth required — internal service-to-service call.
    """
    session = db.query(Session).filter(
        Session.session_id == payload.session_id
    ).first()

    if session:
        session.message_count       = payload.message_count
        session.total_tokens_used   = payload.total_tokens_used
        session.avg_complexity      = payload.avg_complexity
        session.token_budget_status = payload.token_budget_status
        session.topics_discussed    = payload.topics_discussed
        session.files_uploaded      = payload.files_uploaded
    else:
        import uuid as _uuid
        session = Session(
            session_id          = payload.session_id,
            user_id             = _uuid.UUID(payload.user_id) if payload.user_id else None,
            message_count       = payload.message_count,
            total_tokens_used   = payload.total_tokens_used,
            avg_complexity      = payload.avg_complexity,
            token_budget_status = payload.token_budget_status,
            topics_discussed    = payload.topics_discussed,
            files_uploaded      = payload.files_uploaded,
        )
        db.add(session)

    db.commit()
    db.refresh(session)
    return _build_response(session, db)


# ── GET /sessions/{session_id} ─────────────────────────────────────────────────
@router.get("/{session_id}", response_model=SessionResponse, summary="Get a session by ID")
def get_session(
    session_id: str,
    db: DBSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.query(Session).filter(
        Session.session_id == session_id
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found")

    # Access control: owner or admin
    is_owner = session.user_id == current_user.id
    is_admin = current_user.role == "admin"
    if not is_owner and not is_admin:
        raise HTTPException(status_code=403, detail="Access denied")

    return _build_response(session, db)


# ── GET /sessions/user/{user_id} ───────────────────────────────────────────────
@router.get("/user/{user_id}", response_model=UserSessionsResponse, summary="All sessions for a user")
def get_user_sessions(
    user_id: str,
    db: DBSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import uuid as _uuid
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    # Access control: employees can only see their own sessions
    if current_user.role != "admin" and current_user.id != uid:
        raise HTTPException(status_code=403, detail="Access denied")

    sessions = (
        db.query(Session)
        .filter(Session.user_id == uid)
        .order_by(Session.last_active.desc())
        .all()
    )

    return UserSessionsResponse(
        user_id        = user_id,
        total_sessions = len(sessions),
        sessions       = [_build_response(s, db) for s in sessions],
    )
