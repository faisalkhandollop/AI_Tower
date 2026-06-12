"""
keyword_detector.py - Keyword detection engine.

Scans a prompt for FREE and PREMIUM signal keywords and returns a
structured result including matched terms and a net routing signal.
"""

import logging
import re
from dataclasses import dataclass, field

from app.router.config import FREE_KEYWORDS, PREMIUM_KEYWORDS
from app.router.models import KeywordResult

logger = logging.getLogger(__name__)


@dataclass
class KeywordDetectionResult:
    free_hits: list[str] = field(default_factory=list)
    premium_hits: list[str] = field(default_factory=list)

    @property
    def free_count(self) -> int:
        return len(self.free_hits)

    @property
    def premium_count(self) -> int:
        return len(self.premium_hits)

    @property
    def net_signal(self) -> str:
        """
        Returns 'premium', 'free', or 'neutral'.
        Premium always wins when any premium keyword is found
        (one premium keyword outweighs multiple free ones).
        """
        if self.premium_count > 0:
            return "premium"
        if self.free_count > 0:
            return "free"
        return "neutral"

    def to_model(self) -> KeywordResult:
        return KeywordResult(
            free_keywords_found=self.free_hits,
            premium_keywords_found=self.premium_hits,
            net_keyword_signal=self.net_signal,  # type: ignore[arg-type]
        )


class KeywordDetector:
    """
    Scans a prompt against curated FREE and PREMIUM keyword lists.

    Matching is case-insensitive and uses word-boundary–aware regex so that
    e.g. 'email' does not match inside 'emailaddress'.
    """

    def __init__(
        self,
        free_keywords: list[str] | None = None,
        premium_keywords: list[str] | None = None,
    ) -> None:
        self._free_keywords: list[str] = free_keywords or FREE_KEYWORDS
        self._premium_keywords: list[str] = premium_keywords or PREMIUM_KEYWORDS

        # Pre-compile patterns for performance
        self._free_patterns: list[tuple[str, re.Pattern[str]]] = [
            (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
            for kw in self._free_keywords
        ]
        self._premium_patterns: list[tuple[str, re.Pattern[str]]] = [
            (kw, re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE))
            for kw in self._premium_keywords
        ]

        logger.debug(
            "KeywordDetector initialised: %d free keywords, %d premium keywords.",
            len(self._free_keywords),
            len(self._premium_keywords),
        )

    def detect(self, prompt: str) -> KeywordDetectionResult:
        """
        Scan *prompt* and return a KeywordDetectionResult.

        Each keyword is counted at most once, even if it appears multiple
        times in the prompt.
        """
        result = KeywordDetectionResult()

        for keyword, pattern in self._free_patterns:
            if pattern.search(prompt):
                result.free_hits.append(keyword)
                logger.debug("Free keyword matched: '%s'", keyword)

        for keyword, pattern in self._premium_patterns:
            if pattern.search(prompt):
                result.premium_hits.append(keyword)
                logger.debug("Premium keyword matched: '%s'", keyword)

        logger.info(
            "Keyword detection complete — free=%d, premium=%d, signal='%s'.",
            result.free_count,
            result.premium_count,
            result.net_signal,
        )
        return result

    # ── Convenience helpers ────────────────────────────────────────────────────

    def raw_keyword_score(self, prompt: str) -> float:
        """
        Returns a normalised score 0–100 representing keyword-based complexity.

        Logic:
        - Each premium hit adds +15 points (capped at 100).
        - Each free hit subtracts -5 points (floor at 0) – but only when there
          are no premium hits, to avoid penalising genuinely complex prompts
          that happen to contain a simple word.
        """
        result = self.detect(prompt)

        if result.premium_count > 0:
            # 1 hit → 55, 2 → 75, 3 → 88, 4+ → 100
            score = min(45.0 + result.premium_count * 15.0, 100.0)
        elif result.free_count > 0:
            # Start at a moderate baseline and penalise for simplicity signals.
            score = max(25.0 - result.free_count * 5.0, 0.0)
        else:
            score = 40.0  # No signal → slight lean toward medium

        logger.debug("Raw keyword score: %.1f", score)
        return score