"""
smart_model_selector.py - Smart Model Selection V3.

Uses ALL signals to select the best model:
    1. Prompt complexity score (0-100)
    2. Chat history (avg complexity, topic, followup)
    3. File uploads (type, size, pages)
    4. Token budget (ok / warning / critical)

Decision matrix:
    ┌─────────────────┬──────────────────┬─────────────────────┐
    │ Complexity      │ Token Budget     │ Selected Model      │
    ├─────────────────┼──────────────────┼─────────────────────┤
    │ High (71-100)   │ ok               │ Premium flagship    │
    │ High (71-100)   │ warning          │ Premium mid-tier    │
    │ High (71-100)   │ critical         │ Premium efficient   │
    │ Medium (31-70)  │ ok               │ Free capable        │
    │ Medium (31-70)  │ warning/critical │ Free lightweight    │
    │ Low (0-30)      │ any              │ Free lightest       │
    └─────────────────┴──────────────────┴─────────────────────┘
"""

import logging
from dataclasses import dataclass

from app.router.conversation_context import ContextAnalysis

logger = logging.getLogger(__name__)


@dataclass
class ModelSelectionResult:
    recommended_model: str
    route: str                    # "free" | "premium"
    selection_reason: str
    signals_used: list[str]       # which signals drove the decision
    confidence: float
    token_budget_status: str
    complexity_score: int
    final_score: int              # adjusted score after all signals


class SmartModelSelectorV3:
    """
    V3 Model selector — uses 4 signals:
        1. Prompt complexity
        2. Chat history
        3. File context
        4. Token budget
    """

    # ── Model Tiers ────────────────────────────────────────────────────────────

    # Premium models by capability (index 0 = most capable)
    PREMIUM_FLAGSHIP    = "gpt-5"
    PREMIUM_MID         = "claude-opus"
    PREMIUM_EFFICIENT   = "claude-sonnet-4"
    PREMIUM_FAST        = "gemini-pro"

    # Free models by capability
    FREE_CAPABLE        = "codellama-34b"
    FREE_BALANCED       = "mistral-7b"
    FREE_FAST           = "llama-3-8b"
    FREE_LIGHTEST       = "gemma-3"

    # Special purpose models
    MODEL_CSV           = "gpt-5"        # CSV / data analysis
    MODEL_CODE_REVIEW   = "claude-opus"  # Code review
    MODEL_LARGE_DOC     = "claude-opus"  # Large documents
    MODEL_TRANSLATION   = "llama-3-8b"  # Simple translation

    def __init__(self) -> None:
        logger.debug("SmartModelSelectorV3 ready.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def select(
        self,
        complexity_score: int,
        context: ContextAnalysis,
        file_type: str = "",
        page_count: int = 0,
        row_count: int = 0,
    ) -> ModelSelectionResult:
        """
        Select best model using all available signals.
        """
        signals_used: list[str] = []
        score_adjustments: list[tuple[str, int]] = []

        # ── Signal 1: Base complexity score ───────────────────────────────
        final_score = complexity_score
        signals_used.append(f"Prompt complexity: {complexity_score}")

        # ── Signal 2: Chat history adjustment ─────────────────────────────
        if context.previous_complexity > 0:
            # If conversation has been complex, bump up score slightly
            history_boost = int((context.previous_complexity - complexity_score) * 0.2)
            if history_boost > 5:
                final_score += history_boost
                score_adjustments.append(("history", history_boost))
                signals_used.append(
                    f"History complexity boost: +{history_boost} "
                    f"(avg={context.previous_complexity:.0f})"
                )

        if context.is_followup:
            signals_used.append("Followup question — context injected")

        # ── Signal 3: File context adjustment ─────────────────────────────
        file_model_override = ""
        if file_type:
            if file_type == "csv":
                file_model_override = self.MODEL_CSV
                signals_used.append("CSV file → GPT for data analysis")
            elif file_type == "code":
                file_model_override = self.MODEL_CODE_REVIEW
                signals_used.append("Code file → Claude for review")
            elif file_type in ("pdf", "docx") and page_count > 30:
                file_model_override = self.MODEL_LARGE_DOC
                final_score = max(final_score, 75)
                signals_used.append(f"Large document ({page_count} pages) → Claude")
            elif file_type in ("pdf", "docx"):
                signals_used.append(f"Small document ({page_count} pages) — no override")

        # ── Signal 4: Token budget adjustment ─────────────────────────────
        token_penalty = 0
        if context.token_budget_status == "warning":
            token_penalty = 10
            final_score = max(0, final_score - token_penalty)
            signals_used.append(f"Token budget WARNING — score adjusted -{token_penalty}")
        elif context.token_budget_status == "critical":
            token_penalty = 25
            final_score = max(0, final_score - token_penalty)
            signals_used.append(f"Token budget CRITICAL — score adjusted -{token_penalty}")

        final_score = min(100, max(0, final_score))

        # ── Final model selection ──────────────────────────────────────────
        if file_model_override:
            model = file_model_override
            route = "premium"
            reason = f"File type '{file_type}' requires {model}"
            confidence = 0.97
        else:
            model, route, reason, confidence = self._select_by_score(
                final_score,
                context.token_budget_status,
                context.detected_topic,
            )

        logger.info(
            "ModelV3 selected: model='%s' route=%s score=%d→%d budget=%s",
            model, route, complexity_score, final_score,
            context.token_budget_status,
        )

        return ModelSelectionResult(
            recommended_model=model,
            route=route,
            selection_reason=reason,
            signals_used=signals_used,
            confidence=confidence,
            token_budget_status=context.token_budget_status,
            complexity_score=complexity_score,
            final_score=final_score,
        )

    # ── Score-based selection ──────────────────────────────────────────────────

    def _select_by_score(
        self,
        score: int,
        budget: str,
        topic: str,
    ) -> tuple[str, str, str, float]:
        """Returns (model, route, reason, confidence)"""

        # HIGH complexity (71-100)
        if score >= 71:
            if budget == "ok":
                return (
                    self.PREMIUM_FLAGSHIP, "premium",
                    "High complexity + healthy token budget → flagship model",
                    0.96,
                )
            elif budget == "warning":
                return (
                    self.PREMIUM_MID, "premium",
                    "High complexity + token warning → mid-tier premium",
                    0.90,
                )
            else:  # critical
                return (
                    self.PREMIUM_EFFICIENT, "premium",
                    "High complexity + critical tokens → efficient premium",
                    0.85,
                )

        # MEDIUM complexity (31-70)
        elif score >= 31:
            if budget == "ok":
                # Topic-specific selection
                if topic in ("architecture", "security", "devops"):
                    return (
                        self.FREE_CAPABLE, "free",
                        f"Medium complexity + {topic} topic → capable free model",
                        0.88,
                    )
                return (
                    self.FREE_BALANCED, "free",
                    "Medium complexity → balanced free model",
                    0.88,
                )
            else:
                return (
                    self.FREE_FAST, "free",
                    "Medium complexity + token pressure → fast free model",
                    0.82,
                )

        # LOW complexity (0-30)
        else:
            if topic == "general" or budget != "ok":
                return (
                    self.FREE_LIGHTEST, "free",
                    "Low complexity → lightest free model",
                    0.95,
                )
            return (
                self.FREE_FAST, "free",
                "Low complexity → fast free model",
                0.93,
            )