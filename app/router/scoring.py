"""
scoring.py - Multi-dimensional scoring engine.

Produces a composite complexity score 0-100 from seven independent
dimensions.  Each dimension is scored 0-100 and then weighted according
to the values in config.py.
"""

import logging
import re
from dataclasses import dataclass

from app.router.config import settings
from app.router.models import ScoreBreakdown

logger = logging.getLogger(__name__)


# ── Dimension thresholds ───────────────────────────────────────────────────────

# Length scoring: character count → raw score
_LENGTH_BANDS: list[tuple[int, float]] = [
    (50,   5.0),
    (150,  15.0),
    (300,  30.0),
    (600,  50.0),
    (1000, 70.0),
    (2000, 85.0),
    (5000, 95.0),
]

# Multi-step instruction detection: patterns that signal step sequences
_INSTRUCTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(step\s*\d+|first[,\s]|second[,\s]|third[,\s]|then[,\s]|next[,\s]|finally[,\s]|after\s+that)", re.IGNORECASE),
    re.compile(r"\d+\.\s+\w+"),          # numbered lists
    re.compile(r"[-•]\s+\w+"),            # bullet lists
    re.compile(r"\band\s+(also|then)\b", re.IGNORECASE),
]

# Reasoning-complexity signals
_REASONING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(analyze|analyse|evaluate|assess|compare|contrast|justify|critique|reason about)\b", re.IGNORECASE),
    re.compile(r"\b(why|how come|what would happen|trade.?off|pros and cons|implications|consequences)\b", re.IGNORECASE),
    re.compile(r"\b(multi.?step|complex logic|decision tree|flow|algorithm design)\b", re.IGNORECASE),
    re.compile(r"\b(infer|deduce|hypothesis|causal|root cause)\b", re.IGNORECASE),
]

# Coding-complexity signals
_CODING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(implement|build|create|develop|write)\b.{0,40}\b(api|service|module|library|framework|sdk|cli|tool)\b", re.IGNORECASE),
    re.compile(r"\b(algorithm|data structure|tree|graph|heap|trie|dynamic programming|dp|recursion|backtracking)\b", re.IGNORECASE),
    re.compile(r"\b(async|concurrent|thread|process|coroutine|event loop|race condition|deadlock)\b", re.IGNORECASE),
    re.compile(r"\b(unit test|integration test|e2e test|tdd|bdd|mock|fixture|coverage)\b", re.IGNORECASE),
    re.compile(r"\b(refactor|optimiz|performance|profil|benchmark|memory leak)\b", re.IGNORECASE),
    re.compile(r"\b(design pattern|solid|dry|kiss|yagni|clean code)\b", re.IGNORECASE),
]

# Architecture-complexity signals
_ARCHITECTURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(microservice|monolith|service.?oriented|event.?driven|cqrs|event.?sourcing)\b", re.IGNORECASE),
    re.compile(r"\b(scalab\w*|distribut\w*|high.?availab\w*|fault.?toleran\w*|resilient|load.?balanc\w*)\b", re.IGNORECASE),
    re.compile(r"\b(kubernetes|k8s|docker|container|helm|terraform|ansible|ci.?cd)\b", re.IGNORECASE),
    re.compile(r"\b(api.?gateway|service.?mesh|message.?queue|pub.?sub|kafka|rabbitmq|sqs)\b", re.IGNORECASE),
    re.compile(r"\b(data.?warehouse|data.?lake|etl|pipeline|stream.?processing|spark|flink)\b", re.IGNORECASE),
    re.compile(r"\b(security.?audit|threat.?model|penetration|zero.?trust|oauth|oidc)\b", re.IGNORECASE),
    re.compile(r"\b(system.?design|solution.?architect\w*|enterprise|platform.?engineering)\b", re.IGNORECASE),
    re.compile(r"\b(architecture\w*|architectural)\b", re.IGNORECASE),
]

# Context / document signals (large inputs)
_CONTEXT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(repository|codebase|entire|whole|complete|full)\b", re.IGNORECASE),
    re.compile(r"\b(attached|following code|below code|above code|provided code)\b", re.IGNORECASE),
    re.compile(r"\b(document|report|paper|article|book|chapter|section)\b", re.IGNORECASE),
    re.compile(r"\b(multiple files|all files|every file|across the project)\b", re.IGNORECASE),
]


@dataclass
class DimensionScores:
    length: float
    reasoning: float
    coding: float
    architecture: float
    context: float
    instruction_count: float
    keyword: float

    def to_breakdown(self) -> ScoreBreakdown:
        return ScoreBreakdown(
            length_score=round(self.length, 2),
            reasoning_score=round(self.reasoning, 2),
            coding_score=round(self.coding, 2),
            architecture_score=round(self.architecture, 2),
            context_score=round(self.context, 2),
            instruction_count_score=round(self.instruction_count, 2),
            keyword_score=round(self.keyword, 2),
        )

    def weighted_total(self) -> float:
        # Per-dimension sub-scores, each 0-100
        dims = [
            self.length,
            self.reasoning,
            self.coding,
            self.architecture,
            self.context,
            self.instruction_count,
            self.keyword,
        ]
        weights = [
            settings.WEIGHT_LENGTH,
            settings.WEIGHT_REASONING,
            settings.WEIGHT_CODING,
            settings.WEIGHT_ARCHITECTURE,
            settings.WEIGHT_CONTEXT,
            settings.WEIGHT_INSTRUCTION_COUNT,
            settings.WEIGHT_KEYWORD,
        ]
        # Weighted average (weights sum to 100, so divide by 100)
        weighted_avg = sum(d * w for d, w in zip(dims, weights)) / 100.0

        # Complexity bonus: reward prompts with MULTIPLE high-scoring dimensions.
        # Each dimension that scores > 50 contributes to the bonus.
        high_dim_count = sum(1 for d in dims if d > 50)
        complexity_bonus = high_dim_count * 8.0  # up to +56 for 7 dimensions

        # Keyword override: if keyword score is very high (strong premium signal),
        # ensure the total cannot fall below a meaningful floor.
        keyword_floor = self.keyword * 0.9 if self.keyword >= 60 else 0

        result = max(weighted_avg + complexity_bonus, keyword_floor)
        return min(result, 100.0)


class ScoringEngine:
    """
    Converts a raw prompt (plus an optional pre-computed keyword score) into
    a DimensionScores object and a final 0-100 integer composite score.
    """

    # ── Public interface ───────────────────────────────────────────────────────

    def score(self, prompt: str, keyword_score: float = 50.0) -> tuple[int, DimensionScores]:
        """
        Returns (composite_score, DimensionScores).

        :param prompt:        The raw user prompt.
        :param keyword_score: Pre-computed keyword score from KeywordDetector
                              (0–100).  Defaults to neutral 50.
        """
        dims = DimensionScores(
            length=self._score_length(prompt),
            reasoning=self._score_reasoning(prompt),
            coding=self._score_coding(prompt),
            architecture=self._score_architecture(prompt),
            context=self._score_context(prompt),
            instruction_count=self._score_instruction_count(prompt),
            keyword=keyword_score,
        )

        composite = min(int(round(dims.weighted_total())), 100)

        logger.info(
            "Scores — length=%.1f reasoning=%.1f coding=%.1f arch=%.1f "
            "context=%.1f instr=%.1f kw=%.1f  →  composite=%d",
            dims.length, dims.reasoning, dims.coding, dims.architecture,
            dims.context, dims.instruction_count, dims.keyword, composite,
        )
        return composite, dims

    # ── Dimension scorers ──────────────────────────────────────────────────────

    def _score_length(self, prompt: str) -> float:
        char_count = len(prompt)
        for threshold, score in _LENGTH_BANDS:
            if char_count <= threshold:
                return score
        return 100.0

    def _score_reasoning(self, prompt: str) -> float:
        hits = sum(
            1 for pattern in _REASONING_PATTERNS if pattern.search(prompt)
        )
        return min(hits * 25.0, 100.0)

    def _score_coding(self, prompt: str) -> float:
        hits = sum(
            1 for pattern in _CODING_PATTERNS if pattern.search(prompt)
        )
        return min(hits * 20.0, 100.0)

    def _score_architecture(self, prompt: str) -> float:
        hits = sum(
            1 for pattern in _ARCHITECTURE_PATTERNS if pattern.search(prompt)
        )
        return min(hits * 20.0, 100.0)

    def _score_context(self, prompt: str) -> float:
        hits = sum(
            1 for pattern in _CONTEXT_PATTERNS if pattern.search(prompt)
        )
        return min(hits * 30.0, 100.0)

    def _score_instruction_count(self, prompt: str) -> float:
        """Count distinct instruction / step signals in the prompt."""
        hits = sum(
            len(pattern.findall(prompt)) for pattern in _INSTRUCTION_PATTERNS
        )
        return min(hits * 10.0, 100.0)