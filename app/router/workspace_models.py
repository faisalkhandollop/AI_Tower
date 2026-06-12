"""
workspace_models.py - Pydantic schemas for Workspace Intelligence.

Add these to your existing models.py
"""

from pydantic import BaseModel, Field
from typing import Literal


# ── Workspace Route Request ────────────────────────────────────────────────────

class WorkspaceRouteRequest(BaseModel):
    """Request for context-aware routing."""
    prompt: str = Field(..., min_length=1, max_length=10_000)
    session_id: str | None = Field(None, description="Session ID for conversation continuity")


# ── Context Info ───────────────────────────────────────────────────────────────

class ContextInfo(BaseModel):
    session_id: str
    detected_topic: str
    is_followup: bool
    context_summary: str
    previous_complexity: int
    has_file_context: bool
    recent_file: str
    estimated_tokens: int
    token_budget_status: Literal["ok", "warning", "critical"]
    enriched_prompt: str


# ── Model Selection Info ───────────────────────────────────────────────────────

class ModelSelectionInfo(BaseModel):
    recommended_model: str
    route: Literal["free", "premium"]
    selection_reason: str
    signals_used: list[str]
    confidence: float
    final_score: int


# ── Workspace Route Response ───────────────────────────────────────────────────

class WorkspaceRouteResponse(BaseModel):
    """Full context-aware routing response."""
    # Routing decision
    complexity_score: int = Field(..., ge=0, le=100)
    category: Literal["simple", "medium", "complex"]
    route: Literal["free", "premium"]
    recommended_model: str
    confidence: float
    reasons: list[str]

    # Context details
    context: ContextInfo

    # Model selection details
    model_selection: ModelSelectionInfo


# ── Suggested Prompt ───────────────────────────────────────────────────────────

class SuggestedPromptItem(BaseModel):
    title: str
    prompt: str
    category: str
    icon: str


class SuggestionsResponse(BaseModel):
    """Response for /workspace/suggestions endpoint."""
    session_id: str
    suggestions: list[SuggestedPromptItem]
    based_on: str     # "flutter topic | pdf file | default"


# ── Session Info ───────────────────────────────────────────────────────────────

class SessionInfoResponse(BaseModel):
    """Response for /workspace/session/{id} endpoint."""
    session_id: str
    message_count: int
    total_tokens_used: int
    avg_complexity: float
    topics_discussed: list[str]
    files_uploaded: list[str]
    token_budget_status: Literal["ok", "warning", "critical"]