"""
ollama_service.py - Ollama AI integration service.

Sends prompt to deepseek-r1:8b model running at 192.168.1.13:11434
and returns structured routing decision.

Flow:
    route(prompt)
        → _build_prompt()        # prompt engineering
        → httpx POST to Ollama   # AI call with retry
        → _parse_model_response() # extract + clean JSON
        → _normalize_response()  # fix category/route/model
        → AIRouteResponse        # return clean typed object
"""

import json
import logging
import time
from typing import Any

import httpx

from app.router.config import settings, FREE_MODELS, PREMIUM_MODELS, ROUTE_FREE, ROUTE_PREMIUM
from app.router.models import AIRouteResponse

logger = logging.getLogger(__name__)


class OllamaService:
    """Ollama integration service for AI-driven prompt routing."""

    def __init__(self) -> None:
        self._url = settings.OLLAMA_API_URL
        self._model = settings.OLLAMA_MODEL
        self._timeout = settings.OLLAMA_TIMEOUT_SECONDS
        self._retries = settings.OLLAMA_RETRIES
        self._retry_delay = settings.OLLAMA_RETRY_DELAY_SECONDS
        logger.debug("OllamaService configured for %s model=%s", self._url, self._model)

    # ── Public ─────────────────────────────────────────────────────────────────

    def route(self, prompt: str) -> AIRouteResponse:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Prompt must not be empty.")

        payload = {
            "model": self._model,
            "prompt": self._build_prompt(prompt),
            "temperature": 0.0,
            "max_tokens": 512,
            "stream": False,
        }

        last_error: Exception | None = None
        for attempt in range(1, self._retries + 1):
            try:
                logger.info(
                    "OllamaService attempt %d/%d — prompt len=%d.",
                    attempt, self._retries, len(prompt),
                )
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(self._url, json=payload)
                    response.raise_for_status()
                    return self._parse_model_response(response.json(), prompt)

            except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "Ollama attempt %d/%d failed: %s",
                    attempt, self._retries, exc,
                )
                if attempt < self._retries:
                    time.sleep(self._retry_delay * attempt)
                continue

        logger.error("OllamaService exhausted all retries. Last error: %s", last_error)
        return self._fallback_response(prompt)

    # ── Prompt Engineering ─────────────────────────────────────────────────────

    def _build_prompt(self, prompt: str) -> str:
        system_prompt = (
            "You are an expert AI Prompt Complexity Analyzer and Routing Engine.\n\n"

            "## YOUR JOB\n"
            "Analyze the user prompt below and classify its complexity.\n"
            "Then decide which AI model tier (free or premium) should handle it.\n\n"

            "## COMPLEXITY RULES\n"
            "Score 0-30   → category: simple  | Examples: emails, grammar fixes, translation, "
            "summarization, simple writing, basic questions, poems, to-do lists\n"
            "Score 31-70  → category: medium  | Examples: coding help, debugging, API design, "
            "unit tests, SQL queries, explanations with examples, Docker setup\n"
            "Score 71-100 → category: complex | Examples: system design, architecture review, "
            "repository analysis, security audit, RAG pipeline, distributed systems, microservices\n\n"

            "## ROUTE RULES\n"
            "simple  → route: free    → recommended_model: llama-3-8b\n"
            "medium  → route: free    → recommended_model: mistral-7b\n"
            "complex → route: premium → recommended_model: gpt-5\n\n"

            "## SCORING DIMENSIONS\n"
            "Consider these factors when deciding the complexity_score:\n"
            "- Length: longer prompts = higher score\n"
            "- Reasoning: does it need analysis, evaluation, comparison?\n"
            "- Coding: does it need non-trivial implementation?\n"
            "- Architecture: does it involve system design or infra?\n"
            "- Context: does it reference large codebases or documents?\n"
            "- Instructions: does it have multiple steps or requirements?\n"
            "- Keywords: does it contain technical/advanced domain terms?\n\n"

            "## OUTPUT FORMAT\n"
            "Return ONLY this exact JSON. No markdown. No explanation. "
            "No code blocks. No extra text.\n"
            "{\n"
            "  \"complexity_score\": <integer 0-100>,\n"
            "  \"category\": \"<simple|medium|complex>\",\n"
            "  \"route\": \"<free|premium>\",\n"
            "  \"recommended_model\": \"<model name>\",\n"
            "  \"confidence\": <float 0.0-1.0>,\n"
            "  \"reasons\": [\"<specific reason 1>\", \"<specific reason 2>\", \"<specific reason 3>\"]\n"
            "}\n\n"

            "## EXAMPLES\n"
            "Prompt: 'Write a leave application'\n"
            "{\"complexity_score\": 5, \"category\": \"simple\", \"route\": \"free\", "
            "\"recommended_model\": \"llama-3-8b\", \"confidence\": 0.98, "
            "\"reasons\": [\"Simple writing task\", \"No technical knowledge required\", "
            "\"Short single-purpose request\"]}\n\n"

            "Prompt: 'Write a Python class for bank account with deposit and withdraw'\n"
            "{\"complexity_score\": 45, \"category\": \"medium\", \"route\": \"free\", "
            "\"recommended_model\": \"mistral-7b\", \"confidence\": 0.91, "
            "\"reasons\": [\"Moderate coding task\", \"Requires OOP knowledge\", "
            "\"Well-defined scope\"]}\n\n"

            "Prompt: 'Design a scalable microservice architecture for e-commerce with Kubernetes'\n"
            "{\"complexity_score\": 92, \"category\": \"complex\", \"route\": \"premium\", "
            "\"recommended_model\": \"gpt-5\", \"confidence\": 0.97, "
            "\"reasons\": [\"System architecture design required\", "
            "\"Distributed systems knowledge needed\", "
            "\"Multiple components and trade-offs involved\"]}\n\n"

            "## IMPORTANT RULES\n"
            "- reasons must be SPECIFIC to the actual prompt — not generic\n"
            "- confidence must reflect how certain you are about the classification\n"
            "- if prompt is ambiguous, lean toward higher complexity\n"
            "- NEVER return null or empty recommended_model\n"
            "- ALWAYS return exactly 3 reasons\n"
        )

        return f"{system_prompt}User prompt:\n\"{prompt}\"\n"

    # ── Response Parsing ───────────────────────────────────────────────────────

    def _parse_model_response(self, payload: Any, prompt: str) -> AIRouteResponse:
        raw_text = self._extract_text(payload)
        raw_text = self._strip_markdown(raw_text)
        raw_text = self._extract_json_block(raw_text)

        logger.debug("Raw Ollama output: %s", raw_text)

        parsed = json.loads(raw_text)
        if not isinstance(parsed, dict):
            raise ValueError("Ollama did not return a JSON object.")

        normalized = self._normalize_response(parsed, prompt)
        return AIRouteResponse.model_validate(normalized)

    def _extract_text(self, payload: Any) -> str:
        if payload is None:
            raise ValueError("Ollama response payload is empty.")

        if isinstance(payload, str):
            return payload

        if isinstance(payload, dict):
            for key in ("output", "result", "results", "data", "response"):
                if key in payload:
                    return self._extract_text(payload[key])
            if payload:
                first_value = next(iter(payload.values()))
                return self._extract_text(first_value)
            raise ValueError("Ollama response object contained no usable output.")

        if isinstance(payload, list):
            parts: list[str] = []
            for item in payload:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(self._extract_text(item))
                else:
                    parts.append(str(item))
            return "\n".join(parts)

        return str(payload)

    def _strip_markdown(self, text: str) -> str:
        stripped = text.strip()
        # Remove ```json ... ``` or ``` ... ```
        if "```json" in stripped:
            stripped = stripped.split("```json")[1].split("```")[0].strip()
        elif stripped.startswith("```"):
            stripped = stripped.strip("`\n")
        return stripped

    def _extract_json_block(self, text: str) -> str:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise ValueError("No JSON object found in Ollama output.")
        return text[start: end + 1]

    # ── Normalization ──────────────────────────────────────────────────────────

    def _normalize_response(self, parsed: dict[str, Any], prompt: str) -> dict[str, Any]:
        # ── Category normalize ─────────────────────────────────────────────
        # Handle every value Ollama might return
        raw_category = str(parsed.get("category", "simple")).strip().lower()
        category_map = {
            # Standard
            "simple":       "simple",
            "medium":       "medium",
            "complex":      "complex",
            # Sandesh format
            "low":          "simple",
            "high":         "complex",
            # Extra variations
            "easy":         "simple",
            "basic":        "simple",
            "beginner":     "simple",
            "moderate":     "medium",
            "intermediate": "medium",
            "advanced":     "complex",
            "hard":         "complex",
            "difficult":    "complex",
            "expert":       "complex",
        }
        category = category_map.get(raw_category, "simple")

        # ── Route normalize ────────────────────────────────────────────────
        raw_route = str(parsed.get("route", "free")).strip().lower()
        route_map = {
            "free":     "free",
            "premium":  "premium",
            "paid":     "premium",
            "pro":      "premium",
            "advanced": "premium",
            "basic":    "free",
            "standard": "free",
        }
        route = route_map.get(raw_route, "free")

        # ── Ensure route matches category ──────────────────────────────────
        # If AI says complex but route is free — correct it
        if category == "complex" and route == "free":
            logger.warning("Category=complex but route=free — correcting to premium.")
            route = "premium"

        # ── Reasons ────────────────────────────────────────────────────────
        reasons = parsed.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        reasons = [str(r) for r in reasons if str(r).strip()]
        if not reasons:
            reasons = ["AI analysis completed"]

        # ── Complexity score ───────────────────────────────────────────────
        try:
            complexity_score = int(parsed.get("complexity_score", 0))
        except (TypeError, ValueError):
            complexity_score = 0
        complexity_score = max(0, min(complexity_score, 100))

        # ── Confidence ─────────────────────────────────────────────────────
        try:
            confidence = float(parsed.get("confidence", 0.75))
        except (TypeError, ValueError):
            confidence = 0.75
        confidence = max(0.0, min(confidence, 1.0))

        # ── Recommended model ──────────────────────────────────────────────
        recommended_model = str(parsed.get("recommended_model", "")).strip()

        # Model name translation map — 30+ variations Ollama might return
        MODEL_TRANSLATION = {
            # GPT variants
            "gpt-4":            "gpt-5",
            "gpt-4o":           "gpt-5",
            "gpt-4-turbo":      "gpt-5",
            "gpt4":             "gpt-5",
            "gpt-5":            "gpt-5",
            "chatgpt":          "gpt-5",
            # Claude variants
            "claude":           "claude-opus",
            "claude-3":         "claude-opus",
            "claude-3-opus":    "claude-opus",
            "claude-opus":      "claude-opus",
            "claude-opus-4":    "claude-opus-4",
            "claude-sonnet":    "claude-sonnet-4",
            "claude-sonnet-4":  "claude-sonnet-4",
            "claude-haiku":     "claude-opus",
            # Gemini variants
            "gemini":           "gemini-pro",
            "gemini-pro":       "gemini-pro",
            "gemini-1.5-pro":   "gemini-1.5-pro",
            "gemini-ultra":     "gemini-ultra",
            # Llama variants
            "llama":            "llama-3-8b",
            "llama3":           "llama-3-8b",
            "llama-3":          "llama-3-8b",
            "llama-3-8b":       "llama-3-8b",
            "llama-3-70b":      "llama-3-70b",
            "llama2":           "llama-3-8b",
            "llama3.1":         "llama-3.1-8b",
            "llama-3.1-8b":     "llama-3.1-8b",
            # Mistral variants
            "mistral":          "mistral-7b",
            "mistral-7b":       "mistral-7b",
            "mistral-small":    "mistral-7b",
            "mistral-large":    "mistral-large",
            "mistral-nemo":     "mistral-nemo",
            # Groq fallback — map to free
            "groq":             "llama-3-8b",
            # Gemma variants
            "gemma":            "gemma-3",
            "gemma-3":          "gemma-3",
            "gemma-2":          "gemma-2-27b",
            "gemma-2-27b":      "gemma-2-27b",
            # Deepseek variants
            "deepseek":         "deepseek-r1",
            "deepseek-r1":      "deepseek-r1",
            "deepseek-r1:8b":   "deepseek-r1",
            "deepseek-v3":      "deepseek-v3",
            "deepseek-r1-pro":  "deepseek-r1-pro",
            # Other
            "phi":              "phi-3-medium",
            "phi-3":            "phi-3-medium",
            "phi-3-medium":     "phi-3-medium",
            "qwen":             "qwen-2.5-14b",
            "qwen-2.5":         "qwen-2.5-14b",
            "codellama":        "codellama-34b",
            "grok":             "grok-2",
            "grok-2":           "grok-2",
        }

        # Try translation map first
        raw_lower = recommended_model.lower()
        if raw_lower in MODEL_TRANSLATION:
            recommended_model = MODEL_TRANSLATION[raw_lower]
            logger.debug("Model '%s' translated to '%s'.", raw_lower, recommended_model)

        # If model is empty or not in our pools — select from pool
        all_models = list(FREE_MODELS) + list(PREMIUM_MODELS)
        if not recommended_model or recommended_model.lower() == "none":
            recommended_model = self._select_model_from_pool(route, complexity_score)
            logger.warning("Empty model from AI — selected from pool: %s", recommended_model)
        elif recommended_model not in all_models:
            # Unknown model name — select from pool based on route
            logger.warning(
                "Unknown model '%s' from AI — selecting from pool.", recommended_model
            )
            recommended_model = self._select_model_from_pool(route, complexity_score)

        logger.info(
            "Normalized: score=%d category=%s route=%s model=%s confidence=%.2f",
            complexity_score, category, route, recommended_model, confidence,
        )

        return {
            "complexity_score": complexity_score,
            "category":         category,
            "route":            route,
            "recommended_model": recommended_model,
            "confidence":       confidence,
            "reasons":          reasons,
        }

    # ── Model Selection ────────────────────────────────────────────────────────

    def _select_model_from_pool(self, route: str, complexity_score: int) -> str:
        """
        Select best model from pool based on route and score.

        Premium: higher score = more capable model (index 0 = best)
        Free: lower score = lighter model
        """
        if route == ROUTE_PREMIUM:
            models = list(PREMIUM_MODELS)
            if complexity_score >= 85:
                return models[0]   # gpt-5
            elif complexity_score >= 70:
                return models[1]   # gpt-4o
            else:
                return models[3]   # claude-opus
        else:
            models = list(FREE_MODELS)
            if complexity_score <= 15:
                return models[0]   # llama-3-8b
            elif complexity_score <= 30:
                return models[3]   # gemma-3
            else:
                return models[5]   # mistral-7b

    # ── Fallback ───────────────────────────────────────────────────────────────

    def _fallback_response(self, prompt: str) -> AIRouteResponse:
        """
        Called when Ollama is unavailable or all retries exhausted.
        Returns a safe default response using free tier.
        """
        logger.warning(
            "OllamaService fallback — prompt len=%d.", len(prompt)
        )
        model = FREE_MODELS[0] if FREE_MODELS else self._model
        return AIRouteResponse(
            complexity_score=10,
            category="simple",
            route="free",
            recommended_model=model,
            confidence=0.5,
            reasons=[
                "Fallback: AI service unavailable or did not return a valid response.",
                "Using default free-tier routing.",
                "Retry when Ollama server is available.",
            ],
        )