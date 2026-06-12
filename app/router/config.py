"""
config.py - Application configuration and constants.

All service URLs are read from environment variables — no hardcoded IPs.
"""

import os
from typing import Final

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    # Pydantic V2 configuration
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

    APP_NAME: str = "AI Prompt Router"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Ollama configuration
    OLLAMA_API_URL: str = (
        os.getenv("OLLAMA_URL", "http://localhost:11434")
        + "/api/generate"
    )
    OLLAMA_MODEL: str = "deepseek-r1:8b"
    OLLAMA_TIMEOUT_SECONDS: float = 60.0
    OLLAMA_RETRIES: int = 1
    OLLAMA_RETRY_DELAY_SECONDS: float = 2.0

    # Manual engine thresholds
    SIMPLE_MAX_SCORE: int = 30
    MEDIUM_MAX_SCORE: int = 70

    # Scoring weights
    WEIGHT_LENGTH: float = 10.0
    WEIGHT_REASONING: float = 20.0
    WEIGHT_CODING: float = 15.0
    WEIGHT_ARCHITECTURE: float = 20.0
    WEIGHT_CONTEXT: float = 10.0
    WEIGHT_INSTRUCTION_COUNT: float = 10.0
    WEIGHT_KEYWORD: float = 15.0


settings = Settings()

CATEGORY_SIMPLE: Final[str] = "simple"
CATEGORY_MEDIUM: Final[str] = "medium"
CATEGORY_COMPLEX: Final[str] = "complex"

ROUTE_FREE: Final[str] = "free"
ROUTE_PREMIUM: Final[str] = "premium"

# ── Model pools ────────────────────────────────────────────────────────────────

FREE_MODELS: Final[list[str]] = [
    "llama-3-8b",
    "llama-3-70b",
    "llama-3.1-8b",
    "gemma-3",
    "gemma-2-27b",
    "mistral-7b",
    "mistral-nemo",
    "deepseek-r1",
    "deepseek-v3",
    "phi-3-medium",
    "qwen-2.5-14b",
    "codellama-34b",
]

PREMIUM_MODELS: Final[list[str]] = [
    "gpt-5",
    "gpt-4o",
    "gpt-4o-mini",
    "claude-opus",
    "claude-opus-4",
    "claude-sonnet-4",
    "gemini-pro",
    "gemini-1.5-pro",
    "gemini-ultra",
    "mistral-large",
    "deepseek-r1-pro",
    "grok-2",
]

# ── Keyword lists ──────────────────────────────────────────────────────────────

FREE_KEYWORDS: Final[list[str]] = [
    "email", "mail", "e-mail", "compose email", "draft email", "send email",
    "leave application", "casual leave", "sick leave", "earned leave", "half day",
    "thank you note", "thanks note", "appreciation note", "gratitude message",
    "write a letter", "formal letter", "application letter", "request letter",
    "translate", "translation", "convert language", "language conversion",
    "summary", "summarize", "summarise", "tldr", "brief summary", "short summary",
    "rewrite", "paraphrase", "rephrase", "reword", "word it differently",
    "grammar", "proofread", "spell check", "correct grammar", "correct spelling",
    "simplify", "simple explanation", "easy explanation", "explain simply",
    "what is", "define", "definition", "meaning of",
    "calculate", "simple math", "math", "arithmetic", "compute",
    "list of", "give me a list", "bullet points", "template", "sample", "example",
    "simple script", "basic script", "beginner", "starter code",
    "short email", "reply to", "follow up", "follow-up",
    "resignation letter", "cover letter", "bio", "about me",
    "caption", "tagline", "slogan", "headline", "one-liner",
    "reminder", "todo", "to-do", "checklist", "task list",
    "recipe", "steps to", "how to", "procedure", "instructions",
    "simple function", "basic function", "loop", "for loop", "while loop",
    "birthday message", "birthday wish", "greeting", "wishes",
    "poem", "short story", "haiku", "quote",
    "resume", "cv", "job application", "linkedin summary",
    "message", "sms", "whatsapp message", "status update", "announcement",
    "notice", "permission letter", "apology", "sorry message",
    "minutes of meeting", "meeting notes", "agenda",
]

PREMIUM_KEYWORDS: Final[list[str]] = [
    "architecture", "system design", "design pattern", "microservice",
    "microservices", "distributed system", "event-driven",
    "domain driven design", "ddd", "cqrs", "event sourcing",
    "service mesh", "api gateway", "load balancer", "scalability",
    "high availability", "fault tolerance", "disaster recovery",
    "enterprise architecture", "solution architecture",
    "code review", "review this code", "review my code",
    "analyze codebase", "audit codebase", "code quality",
    "code smell", "refactor", "technical debt",
    "security audit", "penetration testing", "vulnerability",
    "threat model", "optimization", "performance tuning",
    "benchmark", "profiling", "bottleneck",
    "infrastructure as code", "terraform", "kubernetes",
    "k8s", "ci/cd pipeline", "data pipeline",
    "data architecture", "data warehouse", "data lake",
    "machine learning pipeline", "mlops", "model deployment",
    "full stack", "end to end", "production grade",
    "production-ready", "multi-tenant", "saas platform",
    "platform engineering", "sdk design",
    "database schema design", "complex query",
    "concurrency", "multithreading",
    "async architecture", "race condition",
    "memory management", "low latency",
    "comprehensive analysis", "deep dive",
    "in-depth analysis", "trade-offs",
    "rag", "retrieval augmented generation",
    "llm", "large language model",
    "fine tuning", "transfer learning",
    "vector database",
    "face detection", "face recognition",
    "computer vision", "image processing",
]