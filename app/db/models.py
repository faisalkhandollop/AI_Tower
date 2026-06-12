import uuid

from sqlalchemy import (
    Boolean,
    Column,
    String,
    Integer,
    Float,
    Text,
    DateTime,
    ForeignKey,
    Numeric,
    Index,
    JSON
)

from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# USERS TABLE
# ─────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name          = Column(String(100), nullable=False)
    email         = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)          # bcrypt hash
    department    = Column(String(100))
    role          = Column(String(50), nullable=False, default="employee")  # employee | admin
    is_active     = Column(Boolean, nullable=False, default=True)
    token_quota   = Column(Integer, nullable=False, default=100_000)
    last_login    = Column(DateTime(timezone=True), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# MODELS TABLE
# ─────────────────────────────────────────────────────────────
class Model(Base):
    __tablename__ = "models"

    id                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model_name         = Column(String(100), nullable=False, unique=True)
    provider           = Column(String(100), nullable=False)
    model_type         = Column(String(20))
    input_cost_per_1k  = Column(Numeric(10, 6))
    output_cost_per_1k = Column(Numeric(10, 6))
    created_at         = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# REQUESTS TABLE
# ─────────────────────────────────────────────────────────────
class Request(Base):
    __tablename__ = "requests"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    model_id        = Column(UUID(as_uuid=True), ForeignKey("models.id"), nullable=True)
    prompt          = Column(Text)
    response_status = Column(String(20))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())


# ─────────────────────────────────────────────────────────────
# SESSIONS TABLE
# ─────────────────────────────────────────────────────────────
class Session(Base):
    __tablename__ = "sessions"

    session_id          = Column(String(100), primary_key=True)
    user_id             = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    message_count       = Column(Integer,     nullable=False, default=0)
    total_tokens_used   = Column(Integer,     nullable=False, default=0)
    avg_complexity      = Column(Float,       nullable=False, default=0.0)
    token_budget_status = Column(String(20),  nullable=False, default="ok")
    topics_discussed    = Column(JSON, nullable=False, default=list)
    files_uploaded      = Column(JSON, nullable=False, default=list)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    last_active         = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_sessions_user_id",    "user_id"),
        Index("ix_sessions_last_active","last_active"),
    )


# ─────────────────────────────────────────────────────────────
# USAGE LOGS TABLE
# ─────────────────────────────────────────────────────────────
class UsageLog(Base):
    __tablename__ = "usage_logs"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_id = Column(UUID(as_uuid=True), ForeignKey("requests.id"),  nullable=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"),     nullable=True)
    model_id   = Column(UUID(as_uuid=True), ForeignKey("models.id"),    nullable=True)
    session_id = Column(String(100),        ForeignKey("sessions.session_id"), nullable=True)

    prompt        = Column(Text,         nullable=False)
    response      = Column(Text,         nullable=False)
    provider      = Column(String(100),  nullable=False)
    model         = Column(String(100),  nullable=True)
    complexity    = Column(String(20),   nullable=False)

    input_tokens  = Column(Integer,      nullable=False)
    output_tokens = Column(Integer,      nullable=False)
    total_tokens  = Column(Integer,      nullable=False)
    cost          = Column(Numeric(10, 6))
    created_at    = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_usage_logs_user_id",    "user_id"),
        Index("ix_usage_logs_session_id", "session_id"),
        Index("ix_usage_logs_provider",   "provider"),
        Index("ix_usage_logs_model",      "model"),
        Index("ix_usage_logs_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────
# ORGANIZATIONS TABLE
# ─────────────────────────────────────────────────────────────
class Organization(Base):
    __tablename__ = "organizations"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name        = Column(String(200), nullable=False, unique=True)
    slug        = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    is_active   = Column(Boolean, nullable=False, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ─────────────────────────────────────────────────────────────
# TEAMS TABLE
# ─────────────────────────────────────────────────────────────
class Team(Base):
    __tablename__ = "teams"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id          = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name            = Column(String(200), nullable=False)
    department      = Column(String(100), nullable=True)
    token_quota     = Column(Integer, nullable=False, default=500_000)
    budget_usd      = Column(Float, nullable=False, default=100.0)
    is_active       = Column(Boolean, nullable=False, default=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_teams_org_id", "org_id"),
    )


# ─────────────────────────────────────────────────────────────
# DEPARTMENTS TABLE
# ─────────────────────────────────────────────────────────────
class Department(Base):
    __tablename__ = "departments"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id          = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    name            = Column(String(200), nullable=False)
    allowed_providers = Column(JSON, nullable=False, default=list)   # ["claude","openai","groq"]
    preferred_provider = Column(String(100), nullable=True)          # "groq"
    routing_policy  = Column(String(50), nullable=False, default="cost_aware")  # cost_aware | quality_first | free_first
    created_at      = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_departments_org_id", "org_id"),
    )


# ─────────────────────────────────────────────────────────────
# USER ROLES / EXTENDED USERS
# ─────────────────────────────────────────────────────────────
# roles: super_admin | admin | manager | employee
# team_id and org_id added to users via a separate mapping table

class UserTeam(Base):
    __tablename__ = "user_teams"

    id         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    team_id    = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    org_id     = Column(UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False)
    role       = Column(String(50), nullable=False, default="employee")  # super_admin | admin | manager | employee
    joined_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_user_teams_user_id", "user_id"),
        Index("ix_user_teams_team_id", "team_id"),
    )


# ─────────────────────────────────────────────────────────────
# TEAM QUOTA USAGE (aggregated daily)
# ─────────────────────────────────────────────────────────────
class TeamUsage(Base):
    __tablename__ = "team_usage"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    team_id       = Column(UUID(as_uuid=True), ForeignKey("teams.id"), nullable=False)
    date          = Column(String(10), nullable=False)   # YYYY-MM-DD
    total_tokens  = Column(Integer, nullable=False, default=0)
    total_cost    = Column(Float,   nullable=False, default=0.0)
    total_requests= Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_team_usage_team_date", "team_id", "date"),
    )


# ─────────────────────────────────────────────────────────────
# PROVIDER KEY VAULT
# ─────────────────────────────────────────────────────────────
class ProviderKey(Base):
    __tablename__ = "provider_keys"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider     = Column(String(100), nullable=False)          # openai | claude | groq | gemini | openrouter
    key_label    = Column(String(200), nullable=False)          # human label e.g. "Production Key"
    api_key_enc  = Column(Text, nullable=False)                 # stored encrypted (base64 XOR for now)
    is_active    = Column(Boolean, nullable=False, default=True)
    priority     = Column(Integer, nullable=False, default=1)   # 1=highest
    added_by     = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    rotated_at   = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), server_default=func.now())
    updated_at   = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_provider_keys_provider", "provider"),
    )


# ─────────────────────────────────────────────────────────────
# PROVIDER HEALTH METRICS
# ─────────────────────────────────────────────────────────────
class ProviderHealth(Base):
    __tablename__ = "provider_health"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider        = Column(String(100), nullable=False)
    checked_at      = Column(DateTime(timezone=True), server_default=func.now())
    is_healthy      = Column(Boolean, nullable=False, default=True)
    latency_ms      = Column(Integer, nullable=True)
    error_message   = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_provider_health_provider_time", "provider", "checked_at"),
    )


# ─────────────────────────────────────────────────────────────
# PROVIDER PRIORITY
# ─────────────────────────────────────────────────────────────
class ProviderPriority(Base):
    __tablename__ = "provider_priorities"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider    = Column(String(100), nullable=False, unique=True)
    priority    = Column(Integer, nullable=False, default=1)   # 1=highest
    is_enabled  = Column(Boolean, nullable=False, default=True)
    updated_by  = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ─────────────────────────────────────────────────────────────
# ALERTS
# ─────────────────────────────────────────────────────────────
class Alert(Base):
    __tablename__ = "alerts"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alert_type  = Column(String(100), nullable=False)  # quota_80 | quota_90 | budget_90 | provider_down
    entity_type = Column(String(50),  nullable=False)  # team | user | provider
    entity_id   = Column(String(100), nullable=False)  # UUID string or provider name
    entity_name = Column(String(200), nullable=True)
    message     = Column(Text, nullable=False)
    is_read     = Column(Boolean, nullable=False, default=False)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_alerts_type",       "alert_type"),
        Index("ix_alerts_entity",     "entity_type", "entity_id"),
        Index("ix_alerts_created_at", "created_at"),
    )


# ─────────────────────────────────────────────────────────────
# USAGE REPORTS (cached daily/weekly/monthly)
# ─────────────────────────────────────────────────────────────
class UsageReport(Base):
    __tablename__ = "usage_reports"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    report_type   = Column(String(20),  nullable=False)   # daily | weekly | monthly
    period_label  = Column(String(50),  nullable=False)   # "2024-01-15" or "2024-W03" or "2024-01"
    scope_type    = Column(String(50),  nullable=False)   # platform | org | team | user
    scope_id      = Column(String(100), nullable=True)    # UUID or null for platform
    data          = Column(JSON,        nullable=False, default=dict)
    generated_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_usage_reports_type_period", "report_type", "period_label"),
    )
