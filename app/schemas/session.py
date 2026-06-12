from pydantic import BaseModel
from typing import Literal, Optional, List
from datetime import datetime


# ─────────────────────────────────────────────────────────────
# UPSERT REQUEST
# Called by Lokesh's gateway after every /workspace/route call.
# Creates the session if it doesn't exist, updates it if it does.
# ─────────────────────────────────────────────────────────────
class SessionUpsert(BaseModel):
    session_id:          str
    user_id:             Optional[str]   = None   # UUID string from Lokesh's request

    # All fields come directly from Sandesh's SessionInfoResponse
    message_count:       int             = 0
    total_tokens_used:   int             = 0
    avg_complexity:      float           = 0.0
    token_budget_status: Literal["ok", "warning", "critical"] = "ok"
    topics_discussed:    List[str]       = []
    files_uploaded:      List[str]       = []


# ─────────────────────────────────────────────────────────────
# SESSION RESPONSE
# Returned from GET /sessions/{session_id}
# ─────────────────────────────────────────────────────────────
class SessionResponse(BaseModel):
    session_id:          str
    user_id:             Optional[str]
    message_count:       int
    total_tokens_used:   int
    avg_complexity:      float
    token_budget_status: str
    topics_discussed:    List[str]
    files_uploaded:      List[str]
    created_at:          datetime
    last_active:         datetime

    # Joined from usage_logs for this session
    total_requests:      int   = 0
    total_cost:          float = 0.0


# ─────────────────────────────────────────────────────────────
# USER SESSIONS RESPONSE
# Returned from GET /sessions/user/{user_id}
# ─────────────────────────────────────────────────────────────
class UserSessionsResponse(BaseModel):
    user_id:       str
    total_sessions: int
    sessions:      List[SessionResponse]