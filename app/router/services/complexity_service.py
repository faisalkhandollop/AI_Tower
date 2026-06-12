"""
services/complexity_service.py - Complexity service layer.

Thin service wrapper around PromptAnalyzer.  Provides a clean seam for
dependency injection, caching, or future A/B testing of analyzers.
"""

import logging

from app.router.analyzer import AnalysisResult, PromptAnalyzer

logger = logging.getLogger(__name__)


class ComplexityService:
    """
    Service that exposes prompt complexity analysis to upper layers.
    All business logic lives in PromptAnalyzer; this class handles
    service-level concerns (lifecycle, logging, error handling).
    """

    def __init__(self, analyzer: PromptAnalyzer | None = None) -> None:
        self._analyzer = analyzer or PromptAnalyzer()
        logger.debug("ComplexityService ready.")

    def get_complexity(self, prompt: str) -> AnalysisResult:
        """
        Analyse *prompt* and return a fully populated AnalysisResult.

        Raises:
            ValueError: if the prompt is empty after stripping.
        """
        cleaned = prompt.strip()
        if not cleaned:
            raise ValueError("Prompt must not be empty.")

        logger.info("ComplexityService: analysing prompt (len=%d).", len(cleaned))
        result = self._analyzer.analyze(cleaned)
        logger.info(
            "ComplexityService: score=%d, category='%s'.",
            result.complexity_score,
            result.category,
        )
        return result