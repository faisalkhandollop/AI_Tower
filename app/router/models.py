"""
models.py - Pydantic request and response schemas with full validation.
"""

from pydantic import BaseModel, Field, field_validator
from typing import Literal


# ── Request ────────────────────────────────────────────────────────────────────

class RouteRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="The user prompt to be analyzed and routed.",
    )

    @field_validator("prompt")
    @classmethod
    def strip_and_validate(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Prompt must not be empty or whitespace only.")
        return v


# ── Sub-models (define FIRST — used by RouteResponse below) ───────────────────

class ScoreBreakdown(BaseModel):
    length_score: float = Field(..., ge=0, le=100, description="Score from prompt length.")
    reasoning_score: float = Field(..., ge=0, le=100, description="Score from reasoning complexity.")
    coding_score: float = Field(..., ge=0, le=100, description="Score from coding complexity.")
    architecture_score: float = Field(..., ge=0, le=100, description="Score from architecture complexity.")
    context_score: float = Field(..., ge=0, le=100, description="Score from context/document size.")
    instruction_count_score: float = Field(..., ge=0, le=100, description="Score from multi-step instructions.")
    keyword_score: float = Field(..., ge=0, le=100, description="Score from keyword detection.")


class KeywordResult(BaseModel):
    free_keywords_found: list[str] = Field(default_factory=list)
    premium_keywords_found: list[str] = Field(default_factory=list)
    net_keyword_signal: Literal["free", "neutral", "premium"]


# ── Manual Engine Response ─────────────────────────────────────────────────────

class RouteResponse(BaseModel):
    """
    Manual scoring engine response.
    Full detail: score_breakdown + keyword_analysis included.
    Used by: POST /route
    """
    prompt_preview: str | None = None
    complexity_score: int = Field(..., ge=0, le=100)
    category: Literal["simple", "medium", "complex"]
    route: Literal["free", "premium"]
    recommended_model: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    score_breakdown: ScoreBreakdown | None = None       # Manual only
    keyword_analysis: KeywordResult | None = None       # Manual only


# ── Ollama AI Response ─────────────────────────────────────────────────────────

class AIRouteResponse(BaseModel):
    """
    Ollama AI engine response.
    Clean output — no manual scoring fields.
    Used by: POST /ai/route
    """
    complexity_score: int = Field(..., ge=0, le=100)
    category: Literal["simple", "medium", "complex"]
    route: Literal["free", "premium"]
    recommended_model: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


# ── File Route Response ────────────────────────────────────────────────────────

class FileRouteResponse(BaseModel):
    """
    File-aware routing response.
    Contains file analysis info + routing decision.
    Used by: POST /route/file
    """
    # File info
    filename: str = Field(..., description="Uploaded file name")
    file_type: str = Field(..., description="pdf | docx | csv | code | image | text | unknown")
    size_kb: float = Field(..., description="File size in KB")
    page_count: int = Field(0, description="PDF / DOCX page count")
    row_count: int = Field(0, description="CSV row count")
    line_count: int = Field(0, description="Code / text line count")

    # Prompt info
    prompt_preview: str = Field(..., description="First 120 chars of user prompt")
    prompt_complexity: str = Field(..., description="low | medium | high")

    # Routing decision
    complexity_score: int = Field(..., ge=0, le=100)
    category: Literal["simple", "medium", "complex"]
    route: Literal["free", "premium"]
    recommended_model: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)

    # What drove the decision
    routed_by: Literal["file", "prompt", "combined", "ai"] = Field(
        ..., description="What drove the routing decision"
    )


# ── Simple Response ────────────────────────────────────────────────────────────

class SimpleRouteResponse(BaseModel):
    """
    Simplified 3-field response.
    Used by: POST /route/simple and POST /ai/route/simple
    """
    complexity: Literal["low", "medium", "high"]
    recommended_model: str
    reason: str


# ── Health / Welcome ───────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    service: str


class WelcomeResponse(BaseModel):
    message: str
    docs_url: str
    version: str