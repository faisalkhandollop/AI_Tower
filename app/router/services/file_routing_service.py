"""
services/file_routing_service.py - File-aware routing service.

Combines:
1. File analysis (FileDetector)
2. Text prompt analysis (OllamaService or manual fallback)

Final routing decision = file signal + prompt signal combined.
"""

import logging
from dataclasses import dataclass

from app.router.file_detector import FileAnalysis, FileDetector, FileType

logger = logging.getLogger(__name__)


@dataclass
class FileRouteResult:
    # File info
    filename: str
    file_type: str
    size_kb: float
    page_count: int
    row_count: int
    line_count: int

    # Prompt info
    prompt_preview: str
    prompt_complexity: str    # low / medium / high

    # Final decision
    complexity_score: int
    category: str             # simple / medium / complex
    route: str                # free / premium
    recommended_model: str
    confidence: float
    reasons: list[str]

    # Who decided
    routed_by: str            # "file" | "prompt" | "combined" | "ai"


class FileRoutingService:
    """
    File-aware routing service.

    Priority:
    1. If file signal is "premium" → always premium (file wins)
    2. If file signal is "free" + prompt is complex → premium (prompt wins)
    3. If both free → free
    4. If file unknown → use prompt analysis only
    """

    # Model preference per file type
    FILE_TYPE_MODELS = {
        FileType.PDF:     "claude-opus",
        FileType.DOCX:    "claude-opus",
        FileType.CSV:     "gpt-5",
        FileType.CODE:    "claude-opus",
        FileType.IMAGE:   "llama-3-8b",
        FileType.TEXT:    "mistral-7b",
        FileType.UNKNOWN: "",
    }

    def __init__(self) -> None:
        self._detector = FileDetector()
        logger.debug("FileRoutingService ready.")

    def route(
        self,
        filename: str,
        file_bytes: bytes,
        prompt: str,
        prompt_route_result=None,   # RouteResponse from manual or AI engine
    ) -> FileRouteResult:
        """
        Analyze file + prompt together and return combined routing decision.

        Args:
            filename:            Uploaded file name
            file_bytes:          Raw file bytes
            prompt:              User text prompt
            prompt_route_result: Already-computed prompt routing result
                                 (from OllamaService or manual router)
        """
        # 1. Analyze file
        file_analysis = self._detector.analyze(filename, file_bytes)

        # 2. Get prompt complexity
        prompt_complexity = "medium"
        prompt_score = 50
        prompt_reasons = []

        if prompt_route_result:
            prompt_complexity = self._map_category(
                getattr(prompt_route_result, "category", "medium")
            )
            prompt_score = getattr(prompt_route_result, "complexity_score", 50)
            prompt_reasons = getattr(prompt_route_result, "reasons", [])

        # 3. Combine signals → final decision
        result = self._combine(
            file_analysis=file_analysis,
            prompt=prompt,
            prompt_complexity=prompt_complexity,
            prompt_score=prompt_score,
            prompt_reasons=prompt_reasons,
        )

        logger.info(
            "FileRouting: file_signal=%s prompt=%s → route=%s model=%s",
            file_analysis.routing_signal,
            prompt_complexity,
            result.route,
            result.recommended_model,
        )
        return result

    # ── Combination Logic ──────────────────────────────────────────────────────

    def _combine(
        self,
        file_analysis: FileAnalysis,
        prompt: str,
        prompt_complexity: str,
        prompt_score: int,
        prompt_reasons: list[str],
    ) -> FileRouteResult:

        reasons: list[str] = []
        route = "free"
        recommended_model = ""
        category = "simple"
        score = prompt_score
        routed_by = "prompt"
        confidence = 0.85

        file_signal = file_analysis.routing_signal

        # ── Rule 1: File is premium → always premium ──────────────────────
        if file_signal == "premium":
            route = "premium"
            recommended_model = file_analysis.recommended_model
            category = "complex"
            score = max(prompt_score, 75)
            routed_by = "file"
            confidence = 0.95
            reasons.append(f"📄 {file_analysis.routing_reason}")
            if prompt_reasons:
                reasons.extend(prompt_reasons[:2])

        # ── Rule 2: File is free but prompt is complex → premium ──────────
        elif file_signal == "free" and prompt_complexity == "high":
            route = "premium"
            recommended_model = file_analysis.recommended_model or "claude-opus"
            category = "complex"
            score = max(prompt_score, 72)
            routed_by = "combined"
            confidence = 0.88
            reasons.append(f"📄 {file_analysis.routing_reason}")
            reasons.append("🧠 Complex prompt upgrades route to premium")
            if prompt_reasons:
                reasons.extend(prompt_reasons[:1])

        # ── Rule 3: Both free → free ──────────────────────────────────────
        elif file_signal == "free":
            route = "free"
            recommended_model = file_analysis.recommended_model or "mistral-7b"
            category = self._score_to_category(prompt_score)
            score = prompt_score
            routed_by = "file"
            confidence = 0.90
            reasons.append(f"📄 {file_analysis.routing_reason}")
            if prompt_reasons:
                reasons.extend(prompt_reasons[:1])

        # ── Rule 4: File unknown → use prompt only ────────────────────────
        else:
            route = "premium" if prompt_complexity == "high" else "free"
            recommended_model = self._model_from_prompt(prompt_complexity)
            category = self._score_to_category(prompt_score)
            score = prompt_score
            routed_by = "prompt"
            confidence = 0.80
            reasons.append("📄 Unknown file type — routed by prompt analysis")
            if prompt_reasons:
                reasons.extend(prompt_reasons[:2])

        return FileRouteResult(
            filename=file_analysis.filename,
            file_type=file_analysis.file_type.value,
            size_kb=file_analysis.size_kb,
            page_count=file_analysis.page_count,
            row_count=file_analysis.row_count,
            line_count=file_analysis.line_count,
            prompt_preview=prompt[:120],
            prompt_complexity=prompt_complexity,
            complexity_score=min(score, 100),
            category=category,
            route=route,
            recommended_model=recommended_model,
            confidence=confidence,
            reasons=reasons,
            routed_by=routed_by,
        )

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _map_category(self, category: str) -> str:
        mapping = {
            "simple": "low", "low": "low",
            "medium": "medium", "moderate": "medium",
            "complex": "high", "high": "high",
        }
        return mapping.get(category.lower(), "medium")

    def _score_to_category(self, score: int) -> str:
        if score <= 30:
            return "simple"
        elif score <= 70:
            return "medium"
        return "complex"

    def _model_from_prompt(self, complexity: str) -> str:
        mapping = {
            "low": "llama-3-8b",
            "medium": "mistral-7b",
            "high": "claude-opus",
        }
        return mapping.get(complexity, "mistral-7b")