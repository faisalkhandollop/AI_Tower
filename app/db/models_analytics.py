"""
app/db/models_analytics.py
===========================
Phase 4 — Executive Analytics Models.

New tables:
    company_usage_summary   — daily pre-aggregated platform-wide stats (org + model grain)
    analytics_snapshots     — point-in-time snapshots for trend charts
    dashboard_widgets       — per-Super-Admin widget layout preferences
    model_benchmarks        — latency / cost / quality benchmarks per model
"""

import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Float,
    Index, Integer, Numeric, JSON, String, Text,
    Enum as SAEnum, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.database import Base


# ─────────────────────────────────────────────────────────────
# COMPANY USAGE SUMMARY
# Pre-aggregated daily stats.  One row per (date, org, provider, model).
# Populated by a background job / cron.  If no job runs, the
# GET /analytics/platform endpoint falls back to live queries.
# ─────────────────────────────────────────────────────────────
class CompanyUsageSummary(Base):
    __tablename__ = "company_usage_summary"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date            = Column(String(10),  nullable=False)                    # YYYY-MM-DD
    organization_id = Column(UUID(as_uuid=True), nullable=True)             # NULL = platform total
    organization_name = Column(String(200), nullable=True)                  # denormalised
    provider        = Column(String(100), nullable=True)                    # NULL = all providers
    model           = Column(String(200), nullable=True)                    # NULL = all models

    total_requests  = Column(Integer,     nullable=False, default=0)
    total_tokens    = Column(Integer,     nullable=False, default=0)
    input_tokens    = Column(Integer,     nullable=False, default=0)
    output_tokens   = Column(Integer,     nullable=False, default=0)
    total_cost_usd  = Column(Numeric(14, 6), nullable=False, default=0)
    active_users    = Column(Integer,     nullable=False, default=0)
    avg_latency_ms  = Column(Float,       nullable=True)
    error_count     = Column(Integer,     nullable=False, default=0)

    computed_at     = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("date", "organization_id", "provider", "model",
                         name="uq_company_usage_summary"),
        Index("ix_cus_date",    "date"),
        Index("ix_cus_org_id",  "organization_id"),
        Index("ix_cus_provider","provider"),
        Index("ix_cus_model",   "model"),
    )


# ─────────────────────────────────────────────────────────────
# ANALYTICS SNAPSHOTS
# Used for historical trend charts (weekly/monthly aggregates).
# snapshot_type: "weekly" | "monthly" | "quarterly"
# ─────────────────────────────────────────────────────────────
SNAPSHOT_TYPES = ("daily", "weekly", "monthly", "quarterly")


class AnalyticsSnapshot(Base):
    __tablename__ = "analytics_snapshots"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_type   = Column(SAEnum(*SNAPSHOT_TYPES, name="snapshot_type_enum"), nullable=False)
    period_label    = Column(String(20),  nullable=False)   # "2026-W22", "2026-05", "2026-Q2"
    scope           = Column(String(50),  nullable=False, default="platform")  # "platform" | org UUID
    data            = Column(JSON,        nullable=False, default=dict)
    # data keys: total_orgs, total_users, total_requests, total_tokens,
    #            total_cost, top_providers (list), top_models (list),
    #            top_orgs (list), new_orgs, churned_orgs, mrr, arr
    generated_at    = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("snapshot_type", "period_label", "scope",
                         name="uq_analytics_snapshot"),
        Index("ix_as_type_period", "snapshot_type", "period_label"),
        Index("ix_as_scope",       "scope"),
    )


# ─────────────────────────────────────────────────────────────
# DASHBOARD WIDGETS
# Saves Super Admin dashboard layout & widget preferences.
# Each admin gets their own config row.
# ─────────────────────────────────────────────────────────────
class DashboardWidget(Base):
    __tablename__ = "dashboard_widgets"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id    = Column(UUID(as_uuid=True), nullable=False)     # references users.id
    widget_key  = Column(String(100), nullable=False)            # e.g. "top_orgs", "mrr_chart"
    position    = Column(Integer,     nullable=False, default=0) # display order
    is_visible  = Column(Boolean,     nullable=False, default=True)
    config      = Column(JSON,        nullable=False, default=dict)
    # config is widget-specific, e.g. { "limit": 10, "date_range": "30d" }
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("admin_id", "widget_key", name="uq_dashboard_widget"),
        Index("ix_dw_admin_id", "admin_id"),
    )


# ─────────────────────────────────────────────────────────────
# MODEL BENCHMARKS
# Periodically sampled latency, cost and reliability stats
# per (provider, model).  One row per sampling window.
# ─────────────────────────────────────────────────────────────
class ModelBenchmark(Base):
    __tablename__ = "model_benchmarks"

    id              = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider        = Column(String(100), nullable=False)
    model           = Column(String(200), nullable=False)
    sampled_at      = Column(DateTime(timezone=True), server_default=func.now())
    sample_window   = Column(String(10),  nullable=False, default="1h")   # 1h | 6h | 24h | 7d

    # Latency
    avg_latency_ms  = Column(Float,    nullable=True)
    p50_latency_ms  = Column(Float,    nullable=True)
    p95_latency_ms  = Column(Float,    nullable=True)
    p99_latency_ms  = Column(Float,    nullable=True)

    # Cost
    avg_cost_per_req= Column(Numeric(10, 8), nullable=True)
    avg_input_cost  = Column(Numeric(10, 8), nullable=True)   # per 1k tokens
    avg_output_cost = Column(Numeric(10, 8), nullable=True)   # per 1k tokens

    # Quality / reliability
    total_requests  = Column(Integer,  nullable=False, default=0)
    error_count     = Column(Integer,  nullable=False, default=0)
    error_rate      = Column(Float,    nullable=True)          # 0.0 – 1.0
    avg_tokens_in   = Column(Float,    nullable=True)
    avg_tokens_out  = Column(Float,    nullable=True)

    # Routing stats
    routing_selections = Column(Integer, nullable=False, default=0)  # how often selected by router
    fallback_count     = Column(Integer, nullable=False, default=0)  # how often used as fallback

    __table_args__ = (
        Index("ix_mb_provider_model",  "provider", "model"),
        Index("ix_mb_sampled_at",      "sampled_at"),
        Index("ix_mb_sample_window",   "sample_window"),
    )
