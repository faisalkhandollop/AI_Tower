"""
suggested_prompts.py - Real-time smart prompt suggestions.

NO hardcoded defaults shown unless nothing else matches.

Real-time signals used:
    1. Session history     — recent prompts + topics
    2. Current topic       — auto-detected from conversation
    3. File uploads        — PDF/CSV/Code specific suggestions
    4. Time of day         — morning standup, evening EOD
    5. Usage patterns      — most used categories from session
    6. Complexity trend    — if user asks complex things → advanced suggestions
    7. Last prompt         — suggest natural next steps
"""

import logging
import time
from dataclasses import dataclass

from app.router.conversation_context import Session

logger = logging.getLogger(__name__)


@dataclass
class SuggestedPrompt:
    title: str
    prompt: str
    category: str
    icon: str
    source: str   # why this was suggested


class SuggestedPromptsEngine:

    # ── Topic-based suggestions ────────────────────────────────────────────────
    TOPIC_SUGGESTIONS: dict[str, list[dict]] = {
        "flutter": [
            {"title": "Flutter Widget", "prompt": "Create a reusable Flutter widget with state management using Provider", "category": "code", "icon": "📱"},
            {"title": "BLoC Pattern", "prompt": "Implement BLoC pattern for state management in Flutter", "category": "code", "icon": "⚡"},
            {"title": "Flutter Architecture", "prompt": "Compare BLoC vs Riverpod vs GetX for Flutter state management", "category": "code", "icon": "🏗️"},
            {"title": "Flutter Testing", "prompt": "Write widget tests for this Flutter component", "category": "code", "icon": "✅"},
            {"title": "Dart Null Safety", "prompt": "Refactor this Dart code to use null safety properly", "category": "code", "icon": "🎯"},
        ],
        "python": [
            {"title": "Python Class", "prompt": "Create a Python class with type hints, docstrings and unit tests", "category": "code", "icon": "🐍"},
            {"title": "FastAPI Endpoint", "prompt": "Write a FastAPI POST endpoint with Pydantic validation and error handling", "category": "code", "icon": "⚡"},
            {"title": "Async Python", "prompt": "Refactor this code to use async/await for better performance", "category": "code", "icon": "🔄"},
            {"title": "Pytest Tests", "prompt": "Write comprehensive pytest unit tests with fixtures and mocks", "category": "code", "icon": "✅"},
            {"title": "Python Optimization", "prompt": "Optimize this Python code for memory and CPU performance", "category": "code", "icon": "🚀"},
        ],
        "architecture": [
            {"title": "System Design", "prompt": "Design a scalable microservice architecture for high traffic", "category": "code", "icon": "🏗️"},
            {"title": "API Design", "prompt": "Design a RESTful API with versioning, auth, and error handling", "category": "code", "icon": "🔌"},
            {"title": "DB Schema", "prompt": "Design an optimized database schema with proper indexes", "category": "code", "icon": "🗄️"},
            {"title": "Event Driven", "prompt": "Design an event-driven architecture using Kafka", "category": "code", "icon": "📨"},
            {"title": "Load Balancing", "prompt": "Design a load balancing strategy for high availability", "category": "code", "icon": "⚖️"},
        ],
        "database": [
            {"title": "Optimize Query", "prompt": "Analyze and optimize this SQL query for better performance", "category": "code", "icon": "⚡"},
            {"title": "Index Strategy", "prompt": "Suggest the best indexing strategy for this database schema", "category": "code", "icon": "🗄️"},
            {"title": "Migration Plan", "prompt": "Create a zero-downtime database migration plan", "category": "code", "icon": "🔄"},
            {"title": "Schema Review", "prompt": "Review this database schema for normalization issues", "category": "code", "icon": "🔍"},
        ],
        "devops": [
            {"title": "Dockerfile", "prompt": "Create a production-ready multi-stage Dockerfile", "category": "code", "icon": "🐳"},
            {"title": "K8s Deploy", "prompt": "Write Kubernetes deployment, service and ingress config", "category": "code", "icon": "☸️"},
            {"title": "CI/CD Pipeline", "prompt": "Write a GitHub Actions CI/CD pipeline with testing and deployment", "category": "code", "icon": "⚙️"},
            {"title": "Terraform IaC", "prompt": "Write Terraform configuration for AWS infrastructure", "category": "code", "icon": "🏗️"},
        ],
        "javascript": [
            {"title": "React Component", "prompt": "Create a React functional component with hooks and TypeScript", "category": "code", "icon": "⚛️"},
            {"title": "API Integration", "prompt": "Write a JavaScript service class to handle REST API calls with error handling", "category": "code", "icon": "🔗"},
            {"title": "TypeScript Types", "prompt": "Define TypeScript interfaces and types for this data structure", "category": "code", "icon": "📘"},
            {"title": "React Testing", "prompt": "Write React Testing Library tests for this component", "category": "code", "icon": "✅"},
        ],
        "ml": [
            {"title": "ML Pipeline", "prompt": "Design an end-to-end machine learning training pipeline", "category": "code", "icon": "🤖"},
            {"title": "Model Evaluation", "prompt": "Write code to evaluate model performance with precision, recall and F1", "category": "code", "icon": "📊"},
            {"title": "Feature Engineering", "prompt": "Suggest feature engineering strategies for this dataset", "category": "analysis", "icon": "🔬"},
            {"title": "MLOps Deploy", "prompt": "Design a MLOps pipeline for model deployment and monitoring", "category": "code", "icon": "🚀"},
        ],
        "security": [
            {"title": "Security Audit", "prompt": "Audit this code for OWASP Top 10 security vulnerabilities", "category": "code", "icon": "🔒"},
            {"title": "Auth Design", "prompt": "Design a secure JWT authentication system with refresh tokens", "category": "code", "icon": "🛡️"},
            {"title": "Threat Model", "prompt": "Create a threat model for this application architecture", "category": "analysis", "icon": "⚠️"},
            {"title": "Pen Test Plan", "prompt": "Create a penetration testing plan for this API", "category": "analysis", "icon": "🔍"},
        ],
        "mobile": [
            {"title": "App Architecture", "prompt": "Design a clean architecture for a mobile application", "category": "code", "icon": "📱"},
            {"title": "Push Notifications", "prompt": "Implement push notification system for iOS and Android", "category": "code", "icon": "🔔"},
            {"title": "Offline Support", "prompt": "Design offline-first data synchronization for mobile app", "category": "code", "icon": "📶"},
        ],
        "general": [
            {"title": "Write Email", "prompt": "Write a professional email for this situation", "category": "writing", "icon": "✉️"},
            {"title": "Summarize", "prompt": "Summarize this content in key bullet points", "category": "document", "icon": "📋"},
            {"title": "Explain Concept", "prompt": "Explain this concept with real-world examples", "category": "writing", "icon": "💡"},
            {"title": "Create Plan", "prompt": "Create a step-by-step action plan for this goal", "category": "writing", "icon": "📝"},
        ],
    }

    # ── File-based suggestions ─────────────────────────────────────────────────
    FILE_SUGGESTIONS: dict[str, list[dict]] = {
        "pdf": [
            {"title": "Summarize Document", "prompt": "Summarize the key points and main findings from this PDF", "category": "document", "icon": "📄"},
            {"title": "Extract Action Items", "prompt": "Extract all action items, deadlines and responsibilities from this document", "category": "document", "icon": "✅"},
            {"title": "Q&A from Doc", "prompt": "Answer these specific questions based on the document content", "category": "document", "icon": "❓"},
            {"title": "Executive Summary", "prompt": "Create a one-page executive summary of this document", "category": "document", "icon": "📊"},
        ],
        "docx": [
            {"title": "Improve Writing", "prompt": "Improve the clarity, tone and professionalism of this document", "category": "document", "icon": "✍️"},
            {"title": "Executive Summary", "prompt": "Create a concise executive summary of this Word document", "category": "document", "icon": "📄"},
            {"title": "Extract Decisions", "prompt": "Extract all decisions, action items and next steps from this document", "category": "document", "icon": "✅"},
            {"title": "Reformat Document", "prompt": "Restructure this document with proper headings and sections", "category": "document", "icon": "📝"},
        ],
        "csv": [
            {"title": "Data Analysis", "prompt": "Analyze this dataset and provide key business insights", "category": "analysis", "icon": "📊"},
            {"title": "Find Anomalies", "prompt": "Identify patterns, outliers and anomalies in this data", "category": "analysis", "icon": "🔍"},
            {"title": "Summary Stats", "prompt": "Calculate and explain summary statistics for all columns", "category": "analysis", "icon": "📈"},
            {"title": "Data Quality", "prompt": "Check this dataset for missing values, duplicates and quality issues", "category": "analysis", "icon": "🧹"},
        ],
        "code": [
            {"title": "Code Review", "prompt": "Review this code for bugs, performance issues and best practices", "category": "code", "icon": "🔍"},
            {"title": "Add Unit Tests", "prompt": "Write comprehensive unit tests for all functions in this file", "category": "code", "icon": "✅"},
            {"title": "Refactor Code", "prompt": "Refactor this code to improve readability, maintainability and performance", "category": "code", "icon": "♻️"},
            {"title": "Add Documentation", "prompt": "Add proper docstrings and inline comments to this code", "category": "code", "icon": "📝"},
        ],
        "image": [
            {"title": "Describe Image", "prompt": "Describe what you see in this image in detail", "category": "analysis", "icon": "🖼️"},
            {"title": "Extract Text", "prompt": "Extract and transcribe all text visible in this image", "category": "analysis", "icon": "📝"},
        ],
    }

    # ── Next-step suggestions based on last prompt ─────────────────────────────
    NEXT_STEP_MAP: list[tuple[list[str], list[dict]]] = [
        (
            ["architecture", "design", "microservice", "system"],
            [
                {"title": "Add Security Layer", "prompt": "Add authentication and authorization layer to this architecture", "category": "code", "icon": "🔒"},
                {"title": "Write ADR", "prompt": "Write an Architecture Decision Record (ADR) for this design", "category": "writing", "icon": "📝"},
                {"title": "Draw Sequence", "prompt": "Create a sequence diagram for the main flows in this architecture", "category": "analysis", "icon": "📊"},
            ]
        ),
        (
            ["review", "audit", "analyze", "check"],
            [
                {"title": "Fix Issues", "prompt": "Fix all the issues found in the previous review", "category": "code", "icon": "🔧"},
                {"title": "Write Tests", "prompt": "Write tests to cover the edge cases found in the review", "category": "code", "icon": "✅"},
                {"title": "Prioritize Issues", "prompt": "Prioritize the found issues by severity and impact", "category": "analysis", "icon": "📋"},
            ]
        ),
        (
            ["function", "class", "implement", "create", "write"],
            [
                {"title": "Add Tests", "prompt": "Write unit tests for the code just created", "category": "code", "icon": "✅"},
                {"title": "Add Docs", "prompt": "Add documentation and docstrings to the code just created", "category": "code", "icon": "📝"},
                {"title": "Optimize", "prompt": "Optimize the code for better performance", "category": "code", "icon": "⚡"},
            ]
        ),
        (
            ["explain", "what is", "how does", "describe"],
            [
                {"title": "Show Example", "prompt": "Show a practical code example of what was just explained", "category": "code", "icon": "💻"},
                {"title": "Compare Options", "prompt": "Compare this approach with alternatives and trade-offs", "category": "analysis", "icon": "⚖️"},
                {"title": "Deep Dive", "prompt": "Give a deeper technical explanation with implementation details", "category": "analysis", "icon": "🔬"},
            ]
        ),
    ]

    def __init__(self) -> None:
        logger.debug("SuggestedPromptsEngine ready.")

    # ── Public API ─────────────────────────────────────────────────────────────

    def generate(
        self,
        session: Session,
        current_topic: str = "general",
        recent_file_type: str = "",
        max_suggestions: int = 6,
    ) -> list[SuggestedPrompt]:
        """
        Generate real-time suggestions — NO hardcoded defaults unless nothing else matches.

        Priority:
        1. Next-step based on last prompt (most contextual)
        2. File-based (if file uploaded)
        3. Topic-based from current topic
        4. Usage-based from session history
        5. Time-based
        6. General only if nothing else fills slots
        """
        suggestions: list[SuggestedPrompt] = []
        seen_titles: set[str] = set()

        def add(items: list[dict], source: str, limit: int = 2) -> None:
            count = 0
            for item in items:
                if item["title"] not in seen_titles and count < limit:
                    suggestions.append(SuggestedPrompt(
                        title=item["title"],
                        prompt=item["prompt"],
                        category=item["category"],
                        icon=item["icon"],
                        source=source,
                    ))
                    seen_titles.add(item["title"])
                    count += 1

        # ── 1. Next-step from last prompt ──────────────────────────────────
        last_msgs = session.last_user_messages
        if last_msgs:
            last_prompt = last_msgs[-1].content.lower()
            for keywords, next_steps in self.NEXT_STEP_MAP:
                if any(kw in last_prompt for kw in keywords):
                    add(next_steps, "next_step", limit=2)
                    break

        # ── 2. File-based ──────────────────────────────────────────────────
        if recent_file_type and recent_file_type in self.FILE_SUGGESTIONS:
            add(self.FILE_SUGGESTIONS[recent_file_type], "file_upload", limit=2)

        # ── 3. Current topic ───────────────────────────────────────────────
        if current_topic and current_topic != "general":
            topic_items = self.TOPIC_SUGGESTIONS.get(current_topic, [])
            add(topic_items, f"topic:{current_topic}", limit=2)

        # ── 4. Usage-based (most used topics in session) ───────────────────
        if session.topic_counts and len(suggestions) < max_suggestions - 1:
            top_topics = sorted(
                session.topic_counts.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            for topic, count in top_topics[:2]:
                if topic != current_topic and topic in self.TOPIC_SUGGESTIONS:
                    if len(suggestions) < max_suggestions - 1:
                        add(self.TOPIC_SUGGESTIONS[topic], f"usage:{topic}", limit=1)

        # ── 5. Time-based ──────────────────────────────────────────────────
        if len(suggestions) < max_suggestions:
            time_items = self._time_based()
            add(time_items, "time_of_day", limit=1)

        # ── 6. Complexity-based (if user asks complex things) ──────────────
        if session.avg_complexity > 60 and len(suggestions) < max_suggestions:
            advanced = [
                {"title": "Architecture Review", "prompt": "Review the architecture of this system and suggest improvements", "category": "code", "icon": "🏗️"},
                {"title": "Performance Audit", "prompt": "Perform a performance audit and suggest optimizations", "category": "code", "icon": "⚡"},
                {"title": "Security Review", "prompt": "Review for security vulnerabilities and suggest hardening", "category": "code", "icon": "🔒"},
            ]
            add(advanced, "complexity_based", limit=1)

        # ── 7. Fill remaining with general (only if still empty) ──────────
        if len(suggestions) < 3:
            general = self.TOPIC_SUGGESTIONS.get("general", [])
            add(general, "general_fallback", limit=max_suggestions - len(suggestions))

        final = suggestions[:max_suggestions]
        logger.info(
            "Generated %d real-time suggestions: topic=%s file=%s sources=%s",
            len(final), current_topic, recent_file_type,
            list({s.source for s in final}),
        )
        return final

    def _time_based(self) -> list[dict]:
        """Real-time time-based suggestions."""
        hour = time.localtime().tm_hour
        if 7 <= hour <= 10:
            return [{"title": "Daily Standup", "prompt": "Write my daily standup: what I completed yesterday, today's plan, any blockers", "category": "meeting", "icon": "☀️"}]
        elif 11 <= hour <= 13:
            return [{"title": "Pre-Meeting Prep", "prompt": "Create a meeting agenda and preparation notes", "category": "meeting", "icon": "📋"}]
        elif 14 <= hour <= 17:
            return [{"title": "Code Review", "prompt": "Review this code for bugs, performance issues and best practices", "category": "code", "icon": "🔍"}]
        elif 17 <= hour <= 20:
            return [{"title": "EOD Summary", "prompt": "Write an end-of-day summary: tasks completed, pending items, tomorrow's priorities", "category": "meeting", "icon": "🌙"}]
        else:
            return [{"title": "Create Docs", "prompt": "Write technical documentation for this feature", "category": "writing", "icon": "📝"}]

    def to_response(self, suggestions: list[SuggestedPrompt]) -> list[dict]:
        return [
            {
                "title": s.title,
                "prompt": s.prompt,
                "category": s.category,
                "icon": s.icon,
                "source": s.source,
            }
            for s in suggestions
        ]