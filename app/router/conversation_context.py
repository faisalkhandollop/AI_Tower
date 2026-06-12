"""
conversation_context.py - Conversation context manager.

Tracks chat history per session and detects:
- Topic continuity (second question relates to first)
- Complexity progression
- File context
- Token usage

Example:
    User: "What is Flutter?"
    User: "Explain state management."
    → Second question is detected as Flutter context → enriched prompt
"""

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Message ────────────────────────────────────────────────────────────────────

@dataclass
class Message:
    role: str                    # "user" | "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)
    complexity_score: int = 0
    recommended_model: str = ""
    file_context: str = ""       # filename if file was uploaded
    token_count: int = 0

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp,
            "complexity_score": self.complexity_score,
            "recommended_model": self.recommended_model,
            "file_context": self.file_context,
            "token_count": self.token_count,
        }


# ── Session ────────────────────────────────────────────────────────────────────

@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[Message] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    total_tokens_used: int = 0
    file_uploads: list[str] = field(default_factory=list)

    # Usage tracking for suggested prompts
    topic_counts: dict[str, int] = field(default_factory=dict)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)
        self.last_active = time.time()
        self.total_tokens_used += message.token_count
        if message.file_context:
            self.file_uploads.append(message.file_context)

    @property
    def last_user_messages(self) -> list[Message]:
        return [m for m in self.messages if m.role == "user"][-5:]

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def avg_complexity(self) -> float:
        scores = [m.complexity_score for m in self.messages if m.complexity_score > 0]
        return sum(scores) / len(scores) if scores else 0.0


# ── Context Analyzer ───────────────────────────────────────────────────────────

@dataclass
class ContextAnalysis:
    enriched_prompt: str          # prompt + context injected
    detected_topic: str           # Flutter, Python, Architecture, etc.
    is_followup: bool             # is this a followup question?
    context_summary: str          # brief summary of conversation so far
    previous_complexity: int      # avg complexity of recent messages
    has_file_context: bool        # was a file uploaded recently?
    recent_file: str              # most recent file name
    estimated_tokens: int         # estimated total tokens
    token_budget_status: str      # "ok" | "warning" | "critical"


class ConversationContextManager:
    """
    Manages conversation sessions and enriches prompts with context.

    Key features:
    - Detects topic continuity between messages
    - Enriches vague followup questions with context
    - Tracks token usage per session
    - Detects file context
    """

    # Token budget thresholds
    TOKEN_WARNING  = 50_000
    TOKEN_CRITICAL = 90_000

    # Session expiry (30 minutes inactivity)
    SESSION_EXPIRY_SECONDS = 1800

    # Topic keywords for detection
    TOPIC_KEYWORDS: dict[str, list[str]] = {
        "flutter":      ["flutter", "dart", "widget", "scaffold", "pubspec"],
        "python":       ["python", "django", "fastapi", "flask", "pip", "pytest"],
        "javascript":   ["javascript", "js", "node", "react", "vue", "typescript"],
        "architecture": ["architecture", "microservice", "system design", "distributed"],
        "database":     ["sql", "postgres", "mysql", "mongodb", "redis", "database"],
        "devops":       ["docker", "kubernetes", "ci/cd", "terraform", "jenkins"],
        "ml":           ["machine learning", "model", "training", "neural", "ai"],
        "security":     ["security", "auth", "oauth", "jwt", "encryption"],
        "mobile":       ["android", "ios", "react native", "flutter", "mobile"],
        "general":      [],
    }

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        logger.debug("ConversationContextManager ready.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_or_create_session(self, session_id: str | None = None) -> Session:
        """Get existing session or create new one."""
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            # Check if session expired
            if time.time() - session.last_active < self.SESSION_EXPIRY_SECONDS:
                logger.debug("Existing session found: %s", session_id)
                return session
            else:
                logger.info("Session expired: %s — creating new.", session_id)

        # Create new session
        session = Session()
        self._sessions[session.session_id] = session
        logger.info("New session created: %s", session.session_id)
        return session

    def analyze_context(
        self,
        prompt: str,
        session: Session,
        file_context: str = "",
    ) -> ContextAnalysis:
        """
        Analyze prompt in context of conversation history.
        Returns enriched prompt and context metadata.
        """
        # Detect topic from prompt + history
        detected_topic = self._detect_topic(prompt, session)

        # Check if this is a followup question
        is_followup = self._is_followup(prompt, session)

        # Build enriched prompt
        enriched_prompt = self._enrich_prompt(prompt, session, detected_topic, is_followup)

        # Context summary
        context_summary = self._build_context_summary(session)

        # Previous complexity
        prev_complexity = int(session.avg_complexity)

        # File context
        recent_file = ""
        has_file = bool(file_context)
        if file_context:
            recent_file = file_context
        elif session.file_uploads:
            recent_file = session.file_uploads[-1]
            has_file = True

        # Token estimation
        estimated_tokens = self._estimate_tokens(session, prompt)
        token_status = self._token_budget_status(estimated_tokens)

        # Track topic usage
        if detected_topic != "general":
            session.topic_counts[detected_topic] = (
                session.topic_counts.get(detected_topic, 0) + 1
            )

        result = ContextAnalysis(
            enriched_prompt=enriched_prompt,
            detected_topic=detected_topic,
            is_followup=is_followup,
            context_summary=context_summary,
            previous_complexity=prev_complexity,
            has_file_context=has_file,
            recent_file=recent_file,
            estimated_tokens=estimated_tokens,
            token_budget_status=token_status,
        )

        logger.info(
            "Context: topic='%s' followup=%s tokens=%d budget=%s",
            detected_topic, is_followup, estimated_tokens, token_status,
        )
        return result

    def add_to_history(
        self,
        session: Session,
        role: str,
        content: str,
        complexity_score: int = 0,
        recommended_model: str = "",
        file_context: str = "",
    ) -> None:
        """Add a message to session history."""
        token_count = self._estimate_token_count(content)
        msg = Message(
            role=role,
            content=content,
            complexity_score=complexity_score,
            recommended_model=recommended_model,
            file_context=file_context,
            token_count=token_count,
        )
        session.add_message(msg)
        logger.debug(
            "Added %s message to session %s (tokens=%d)",
            role, session.session_id, token_count,
        )

    # ── Topic Detection ────────────────────────────────────────────────────────

    def _detect_topic(self, prompt: str, session: Session) -> str:
        """Detect topic from current prompt + recent history."""
        combined_text = prompt.lower()

        # Include last 3 user messages for context
        for msg in session.last_user_messages[-3:]:
            combined_text += " " + msg.content.lower()

        # Score each topic
        topic_scores: dict[str, int] = {}
        for topic, keywords in self.TOPIC_KEYWORDS.items():
            if not keywords:
                continue
            score = sum(1 for kw in keywords if kw in combined_text)
            if score > 0:
                topic_scores[topic] = score

        if not topic_scores:
            return "general"

        # Return highest scoring topic
        return max(topic_scores, key=lambda t: topic_scores[t])

    # ── Followup Detection ─────────────────────────────────────────────────────

    def _is_followup(self, prompt: str, session: Session) -> bool:
        """
        Detect if current prompt is a followup to previous messages.

        Signals:
        - Short prompt (< 8 words)
        - Contains pronouns: it, this, that, they, its
        - Contains "more", "also", "what about", "how about"
        - No clear topic keyword but session has history
        """
        if session.message_count == 0:
            return False

        prompt_lower = prompt.lower().strip()
        words = prompt_lower.split()

        # Short prompt = likely followup
        if len(words) <= 7:
            followup_signals = [
                "it", "this", "that", "they", "its", "their",
                "more", "also", "and", "but", "so", "explain",
                "what about", "how about", "why", "when", "where",
                "elaborate", "continue", "next", "further",
            ]
            if any(signal in prompt_lower for signal in followup_signals):
                return True

        # Ambiguous short question with history
        if len(words) <= 5 and session.message_count >= 2:
            return True

        return False

    # ── Prompt Enrichment ──────────────────────────────────────────────────────

    def _enrich_prompt(
        self,
        prompt: str,
        session: Session,
        topic: str,
        is_followup: bool,
    ) -> str:
        """
        Inject conversation context into prompt so router understands it better.

        Example:
            History: "What is Flutter?"
            Current: "Explain state management."
            Enriched: "In context of Flutter development: Explain state management."
        """
        if not is_followup or session.message_count == 0:
            return prompt

        # Build context prefix from recent messages
        recent_msgs = session.last_user_messages[-3:]
        if not recent_msgs:
            return prompt

        # Get last topic keywords
        context_parts = []
        for msg in recent_msgs:
            if len(msg.content) > 10:
                context_parts.append(msg.content[:80])

        if not context_parts:
            return prompt

        context_str = " | ".join(context_parts)

        enriched = (
            f"[Conversation context: {context_str}] "
            f"Current question: {prompt}"
        )

        logger.debug("Enriched prompt: %s", enriched[:100])
        return enriched

    # ── Context Summary ────────────────────────────────────────────────────────

    def _build_context_summary(self, session: Session) -> str:
        """Build short summary of conversation so far."""
        if session.message_count == 0:
            return "New conversation"

        user_msgs = session.last_user_messages
        if not user_msgs:
            return "No user messages yet"

        topics = list(session.topic_counts.keys())
        topic_str = ", ".join(topics[:3]) if topics else "general"

        return (
            f"{session.message_count} messages | "
            f"Topics: {topic_str} | "
            f"Avg complexity: {session.avg_complexity:.0f}"
        )

    # ── Token Estimation ───────────────────────────────────────────────────────

    def _estimate_tokens(self, session: Session, new_prompt: str) -> int:
        """Estimate total tokens including history + new prompt."""
        return session.total_tokens_used + self._estimate_token_count(new_prompt)

    def _estimate_token_count(self, text: str) -> int:
        """Rough token estimate: ~4 chars per token."""
        return max(1, len(text) // 4)

    def _token_budget_status(self, total_tokens: int) -> str:
        if total_tokens >= self.TOKEN_CRITICAL:
            return "critical"
        elif total_tokens >= self.TOKEN_WARNING:
            return "warning"
        return "ok"