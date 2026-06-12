import logging

from app.router.analyzer import AnalysisResult
from app.router.config import (
    CATEGORY_COMPLEX,
    CATEGORY_MEDIUM,
    FREE_MODELS,
    PREMIUM_MODELS,
    ROUTE_FREE,
    ROUTE_PREMIUM,
    settings,
)
from app.router.models import RouteResponse

logger = logging.getLogger(__name__)


class RoutingService:

    def __init__(self) -> None:
        logger.debug("RoutingService ready.")

    def route(self, analysis: AnalysisResult) -> RouteResponse:
        route = self._determine_route(
            analysis.category,
            keyword_signal=analysis.keyword_result.net_signal,
            score=analysis.complexity_score,
        )
        model = self._select_model(route, analysis.complexity_score)

        logger.info(
            "Routing: category='%s' score=%d route='%s' model='%s'.",
            analysis.category, analysis.complexity_score, route, model,
        )

        return RouteResponse(
            prompt_preview=analysis.prompt[:120],
            complexity_score=analysis.complexity_score,
            category=analysis.category,
            route=route,
            recommended_model=model,
            confidence=analysis.confidence,
            reasons=analysis.reasons,
            score_breakdown=analysis.score_breakdown,
            keyword_analysis=analysis.keyword_model,
        )

    # ── Route Decision ─────────────────────────────────────────────────────────

    def _determine_route(
        self,
        category: str,
        keyword_signal: str = "neutral",
        score: int = 0,
    ) -> str:

        # Normalize — every possible value Ollama or manual engine might return
        category_map = {
            "simple":       "simple",
            "medium":       "medium",
            "complex":      "complex",
            "low":          "simple",
            "high":         "complex",
            "easy":         "simple",
            "basic":        "simple",
            "moderate":     "medium",
            "intermediate": "medium",
            "advanced":     "complex",
            "hard":         "complex",
            "difficult":    "complex",
        }
        normalized = category_map.get(category.strip().lower(), "simple")

        if normalized == "complex":
            return ROUTE_PREMIUM

        # Medium + strong premium keyword signal → upgrade to premium
        if normalized == "medium" and keyword_signal == "premium" and score >= 55:
            return ROUTE_PREMIUM

        return ROUTE_FREE

    # ── Model Selection ────────────────────────────────────────────────────────

    def _select_model(self, route: str, score: int, raw_model: str = "") -> str:
        """
        Select model from pool proportionally based on score.

        Score 0-100 is mapped across the full model pool so that
        ALL models get used — not just index 0,1,2.

        FREE pool (score 0-100 mapped across all free models):
            Score 0-8   → llama-3-8b      (lightest)
            Score 9-16  → llama-3-70b
            Score 17-24 → llama-3.1-8b
            Score 25-32 → gemma-3
            Score 33-40 → gemma-2-27b
            Score 41-49 → mistral-7b
            Score 50-57 → mistral-nemo
            Score 58-65 → deepseek-r1
            Score 66-74 → deepseek-v3
            Score 75-82 → phi-3-medium
            Score 83-91 → qwen-2.5-14b
            Score 92-100→ codellama-34b  (most capable free)

        PREMIUM pool (score 71-100 mapped across all premium models):
            Score 71-76 → gpt-5           (flagship)
            Score 77-82 → gpt-4o
            Score 83-86 → gpt-4o-mini
            Score 87-89 → claude-opus
            Score 90-92 → claude-opus-4
            Score 93-95 → claude-sonnet-4
            Score 96-97 → gemini-pro
            Score 98    → gemini-1.5-pro
            Score 99    → gemini-ultra
            Score 100   → grok-2
        """

        if route == ROUTE_FREE:
            pool = list(FREE_MODELS)
            model = self._proportional_select(pool, score, min_score=0, max_score=100)
            logger.debug("FREE model selected: '%s' for score=%d", model, score)
            return model

        else:  # PREMIUM
            pool = list(PREMIUM_MODELS)
            # Premium scores are typically 55-100
            # Map 55-100 range across full premium pool
            model = self._proportional_select(pool, score, min_score=55, max_score=100)
            logger.debug("PREMIUM model selected: '%s' for score=%d", model, score)
            return model

    def _proportional_select(
        self,
        pool: list[str],
        score: int,
        min_score: int = 0,
        max_score: int = 100,
    ) -> str:
        """
        Map score proportionally across pool.

        Example:
            pool = [A, B, C, D]  (4 models)
            score=10 → index 0 → A
            score=35 → index 1 → B
            score=60 → index 2 → C
            score=85 → index 3 → D
        """
        if not pool:
            return "llama-3-8b"

        # Clamp score
        score = max(min_score, min(score, max_score))

        # Normalize score to 0.0-1.0
        score_range = max_score - min_score
        if score_range == 0:
            normalized = 0.0
        else:
            normalized = (score - min_score) / score_range

        # Map to pool index
        index = int(normalized * len(pool))
        index = min(index, len(pool) - 1)  # clamp to last index

        selected = pool[index]
        logger.debug(
            "Proportional select: score=%d normalized=%.2f index=%d model='%s'",
            score, normalized, index, selected,
        )
        return selected