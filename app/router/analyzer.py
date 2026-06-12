"""
analyzer.py - Prompt complexity analyzer.

Orchestrates KeywordDetector + ScoringEngine and produces a structured
AnalysisResult that the routing layer can consume directly.
"""

import logging
from dataclasses import dataclass, field

from app.router.config import (
    CATEGORY_COMPLEX,
    CATEGORY_MEDIUM,
    CATEGORY_SIMPLE,
    settings,
)
from app.router.keyword_detector import KeywordDetector, KeywordDetectionResult
from app.router.models import KeywordResult, ScoreBreakdown
from app.router.scoring import DimensionScores, ScoringEngine

logger = logging.getLogger(__name__)


@dataclass
class AnalysisResult:
    prompt: str
    complexity_score: int
    category: str
    score_breakdown: ScoreBreakdown
    keyword_result: KeywordDetectionResult
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def keyword_model(self) -> KeywordResult:
        return self.keyword_result.to_model()


class PromptAnalyzer:
    """
    High-level orchestrator that accepts a raw prompt and returns an
    AnalysisResult containing all scoring data needed for routing.
    """

    def __init__(self) -> None:
        self._detector = KeywordDetector()
        self._scorer = ScoringEngine()
        logger.debug("PromptAnalyzer initialised.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(self, prompt: str) -> AnalysisResult:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Prompt must not be empty or whitespace only.")

        logger.info("Analyzing prompt (%d chars): %.80s…", len(prompt), prompt)

        # 1. Keyword detection
        kw_result = self._detector.detect(prompt)
        kw_score = self._detector.raw_keyword_score(prompt)

        # 2. Multi-dimensional scoring
        composite, dims = self._scorer.score(prompt, keyword_score=kw_score)

        # 3. Categorise
        category = self._categorize(composite)

        # 4. Confidence
        confidence = self._compute_confidence(composite, category)

        # 5. Human-readable reasons
        reasons = self._build_reasons(prompt, composite, dims, kw_result)

        result = AnalysisResult(
            prompt=prompt,
            complexity_score=composite,
            category=category,
            score_breakdown=dims.to_breakdown(),
            keyword_result=kw_result,
            reasons=reasons,
            confidence=confidence,
        )

        logger.info(
            "Analysis complete — score=%d category='%s' confidence=%.2f",
            composite,
            category,
            confidence,
        )
        return result

    # ── Private helpers ────────────────────────────────────────────────────────

    def _categorize(self, score: int) -> str:
        if score <= settings.SIMPLE_MAX_SCORE:
            return CATEGORY_SIMPLE
        if score <= settings.MEDIUM_MAX_SCORE:
            return CATEGORY_MEDIUM
        return CATEGORY_COMPLEX

    def _compute_confidence(self, score: int, category: str) -> float:
        """
        Confidence is highest when the score is far from a boundary and
        lowest when it sits near a threshold (30 or 70).
        """
        boundaries = [settings.SIMPLE_MAX_SCORE, settings.MEDIUM_MAX_SCORE]
        min_distance = min(abs(score - b) for b in boundaries)

        # Normalise: distance 0 → 0.5 confidence; distance ≥ 25 → 1.0
        raw = 0.5 + (min_distance / 25.0) * 0.5
        return round(min(raw, 1.0), 2)

    def _build_reasons(
        self,
        prompt: str,
        score: int,
        dims: DimensionScores,
        kw: KeywordDetectionResult,
    ) -> list[str]:
        reasons: list[str] = []

        # Keyword signals
        if kw.premium_count > 0:
            kws = ", ".join(f"'{k}'" for k in kw.premium_hits[:5])
            reasons.append(f"Premium keyword(s) detected: {kws}")
        elif kw.free_count > 0:
            kws = ", ".join(f"'{k}'" for k in kw.free_hits[:3])
            reasons.append(f"Simple-task keyword(s) detected: {kws}")

        # Length
        if dims.length >= 70:
            reasons.append(f"Large prompt ({len(prompt)} chars) indicates high context load")
        elif dims.length <= 15:
            reasons.append("Short prompt indicates a simple, narrow task")

        # Reasoning
        if dims.reasoning >= 50:
            reasons.append("High reasoning / analytical complexity detected")

        # Coding
        if dims.coding >= 40:
            reasons.append("Non-trivial coding task identified")

        # Architecture
        if dims.architecture >= 40:
            reasons.append("System / architecture complexity detected")

        # Context
        if dims.context >= 30:
            reasons.append("Prompt references large context (repository, document, codebase)")

        # Instructions
        if dims.instruction_count >= 30:
            reasons.append("Multi-step or multi-part instructions detected")

        # Fallback
        if not reasons:
            if score <= settings.SIMPLE_MAX_SCORE:
                reasons.append("Prompt is short, straightforward, and contains no complex signals")
            else:
                reasons.append("Moderate complexity detected across multiple dimensions")

        return reasons