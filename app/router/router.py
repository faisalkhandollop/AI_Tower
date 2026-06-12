"""
router.py - Main prompt router.

PromptRouter = MANUAL engine (keyword scoring + 7 dimensions)
               No Ollama, no network, works offline always.

Used by:
    POST /route         → full manual output
    POST /route/simple  → simple 3-field manual output
"""

import logging

from app.router.models import RouteResponse
from app.router.services.complexity_service import ComplexityService
from app.router.services.routing_service import RoutingService

logger = logging.getLogger(__name__)


class PromptRouter:
    """
    Manual scoring engine orchestrator.

    Flow:
        prompt
            → ComplexityService (analyzer + keyword_detector + scoring)
            → RoutingService (routing rules + model selection)
            → RouteResponse (with score_breakdown + keyword_analysis)
    """

    def __init__(
        self,
        complexity_service: ComplexityService | None = None,
        routing_service: RoutingService | None = None,
    ) -> None:
        self._complexity = complexity_service or ComplexityService()
        self._routing = routing_service or RoutingService()
        logger.debug("PromptRouter (manual engine) ready.")

    def route(self, prompt: str) -> RouteResponse:
        """
        Analyze prompt using manual engine and return RouteResponse.

        No Ollama, no network calls.
        Returns full score_breakdown + keyword_analysis.
        """
        logger.info("PromptRouter.route() — prompt len=%d.", len(prompt))

        # 1. Analyze complexity (manual scoring)
        analysis = self._complexity.get_complexity(prompt)

        # 2. Apply routing rules → RouteResponse
        response = self._routing.route(analysis)

        logger.info(
            "PromptRouter result: score=%d route='%s' model='%s'.",
            response.complexity_score,
            response.route,
            response.recommended_model,
        )
        return response


def route_prompt(prompt: str) -> dict:
    """
    Sandesh's simple interface.

    Usage:
        result = route_prompt("Write a leave email")
        # {"complexity": "low", "recommended_model": "llama-3-8b", "reason": "..."}
    """
    router = PromptRouter()
    result = router.route(prompt)

    complexity_map = {
        "simple": "low",
        "medium": "medium",
        "complex": "high",
    }
    reason_map = {
        "simple": "simple content generation",
        "medium": "moderate complexity task",
        "complex": "high complexity — architecture / analysis / large context",
    }
    return {
        "complexity": complexity_map.get(result.category, "medium"),
        "recommended_model": result.recommended_model,
        "reason": reason_map.get(result.category, "moderate complexity task"),
    }