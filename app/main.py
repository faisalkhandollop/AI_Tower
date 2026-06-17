"""
main.py - Tower AI Enterprise Platform v5.7 (Phase 7 Security Operations Center)

What changed from v5.6:
  - Added /security/logins, /security/mfa, /security/tokens, /security/sessions
  - Security models registered for create_all()
  - All prior routers preserved, no endpoints removed
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── v4 Routers (ALL PRESERVED) ────────────────────────────────────────────────
from app.api.auth            import router as auth_router
from app.api.users           import router as users_router
from app.api.usage           import router as usage_router
from app.api.admin           import router as admin_router
from app.api.sessions        import router as sessions_router
from app.api.chat            import router as chat_router
from app.api.routing         import router as routing_router
from app.api.vault           import router as vault_router
from app.api.provider_health import router as provider_health_router
from app.api.org             import router as org_router
from app.api.alerts          import router as alerts_router
from app.api.reports         import router as reports_router

# ── Phase 1 Enterprise Routers ────────────────────────────────────────────────
from app.api.conversations   import router as conversations_router
from app.api.audit           import router as audit_router
from app.api.subscriptions   import router as subscriptions_router

# ── Phase 2 Billing & Revenue Router ─────────────────────────────────────────
from app.api.billing         import router as billing_router

# ── Phase 3 Routing Governance Router ────────────────────────────────────────
from app.api.routing_governance import router as routing_governance_router

# ── Phase 4 Executive Analytics Router ───────────────────────────────────────
from app.api.analytics import router as analytics_router

# ── Phase 5 Privacy Enforcement Router ───────────────────────────────────────
from app.api.privacy import router as privacy_router

# ── Phase 6 Support Access Workflow Router ────────────────────────────────────
from app.api.support import router as support_router

# ── Phase 7 Security Operations Center Router (NEW) ───────────────────────────
from app.api.security import router as security_router

# ── DB ────────────────────────────────────────────────────────────────────────
from app.db.database import engine, Base, SessionLocal
from app.db import models                # existing models
from app.db import models_enterprise     # Phase 1 models  # noqa: F401
from app.db import models_billing        # Phase 2 models  # noqa: F401
from app.db import models_routing        # Phase 3 models  # noqa: F401
from app.db import models_analytics      # Phase 4 models  # noqa: F401
from app.db import models_privacy        # Phase 5 models  # noqa: F401
from app.db import models_support        # Phase 6 models  # noqa: F401
from app.db import models_security       # Phase 7 models  # noqa: F401

# ── Seed services ─────────────────────────────────────────────────────────────
from app.services.rbac              import seed_roles_and_permissions
from app.services.routing_governance import seed_categories

import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create all tables (existing + new)
Base.metadata.create_all(bind=engine)

# Seed roles, permissions, and prompt categories on every startup (idempotent)
try:
    _seed_db = SessionLocal()
    seed_roles_and_permissions(_seed_db)
    seed_categories(_seed_db)
    _seed_db.close()
    logger.info("RBAC: roles and permissions seeded.")
    logger.info("Routing governance: prompt categories seeded.")
except Exception as e:
    logger.warning(f"Seed skipped: {e}")

app = FastAPI(
    title="Tower AI — Enterprise Platform",
    version="5.7.0",
    description=(
        "Unified AI Platform v5.7 — Phase 7 Security Operations Center.\n\n"
        "**Control Tower:** `/auth` `/users` `/usage` `/admin` `/sessions`\n\n"
        "**AI Gateway:** `/chat` (smart routing, escalation, dept/budget aware)\n\n"
        "**AI Router:** `/route` `/ai/route` `/workspace` `/models` `/stats`\n\n"
        "**API Key Vault:** `/vault/keys` `/vault/priorities` (AES-256-GCM)\n\n"
        "**Provider Health:** `/providers/health` `/providers/dashboard`\n\n"
        "**Organizations:** `/org` (orgs, teams, departments, user roles)\n\n"
        "**Alerts:** `/alerts` (quota 80/90%, budget, provider down)\n\n"
        "**Reports:** `/reports/daily` `/reports/weekly` `/reports/monthly`\n\n"
        "**Conversations:** `/conversations` (message history)\n\n"
        "**Audit Logs:** `/audit` (immutable audit trail)\n\n"
        "**RBAC:** `/rbac` (roles and permissions)\n\n"
        "**Subscriptions:** `/subscriptions` (plans & subscription management)\n\n"
        "**Billing:** `/billing/invoices` `/billing/payments` `/billing/dashboard`\n\n"
        "**Routing Governance:** `/routing/rules` `/routing/logs` `/routing/feedback` `/routing/analytics`\n\n"
        "**Executive Analytics:** `/analytics/platform` `/analytics/providers` `/analytics/organizations` `/analytics/dashboard`\n\n"
        "**Privacy & Data Governance:** `/privacy/settings` `/privacy/policies` `/privacy/export-logs` `/privacy/super-admin-blocks`\n\n"
        "**Support Access Workflow:** `/support/request` `/support/approve` `/support/revoke` `/support/history`\n\n"
        "**Security Operations Center:** `/security/logins` `/security/mfa` `/security/tokens` `/security/sessions` (NEW)\n\n"
    ),
)

allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── v4 routers ────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(usage_router)
app.include_router(admin_router)
app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(routing_router)
app.include_router(vault_router)
app.include_router(provider_health_router)
app.include_router(org_router)
app.include_router(alerts_router)
app.include_router(reports_router)

# ── Phase 1 enterprise routers ────────────────────────────────────────────────
app.include_router(conversations_router)
app.include_router(audit_router)
app.include_router(subscriptions_router)

# ── Phase 2 billing router ────────────────────────────────────────────────────
app.include_router(billing_router)

# ── Phase 3 routing governance router ────────────────────────────────────────
app.include_router(routing_governance_router)

# ── Phase 4 executive analytics router ───────────────────────────────────────
app.include_router(analytics_router)

# ── Phase 5 privacy enforcement router ───────────────────────────────────────
app.include_router(privacy_router)

# ── Phase 6 support access workflow router ───────────────────────────────────
app.include_router(support_router)

# ── Phase 7 security operations center router ────────────────────────────────
app.include_router(security_router)

# ── Frontend SPA ──────────────────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/", tags=["General"])
def home():
    return {
        "message": "Tower AI Enterprise Platform v5.7",
        "phase":   "Phase 7 Security Operations Center",
        "docs":    "/docs",
        "services": {
            "control_tower":       ["/auth", "/users", "/usage", "/admin", "/sessions"],
            "ai_gateway":          ["/chat"],
            "ai_router":           ["/route", "/ai/route", "/workspace", "/compare", "/models"],
            "api_key_vault":       ["/vault/keys", "/vault/priorities"],
            "provider_health":     ["/providers/health", "/providers/dashboard"],
            "organizations":       ["/org"],
            "alerts":              ["/alerts"],
            "reports":             ["/reports/daily", "/reports/weekly", "/reports/monthly"],
            "conversations":       ["/conversations"],
            "audit":               ["/audit", "/audit/privileged"],
            "subscriptions":       ["/subscriptions/plans", "/subscriptions", "/subscriptions/dashboard"],
            "billing":             ["/billing/invoices", "/billing/payments", "/billing/dashboard"],
            "routing_governance":  [
                "/routing/rules",
                "/routing/logs",
                "/routing/feedback",
                "/routing/analytics",
                "/routing/dashboard",
                "/routing/categories",
            ],
            "executive_analytics": [
                "/analytics/platform",
                "/analytics/providers",
                "/analytics/organizations",
                "/analytics/trends",
                "/analytics/benchmarks",
                "/analytics/dashboard",
                "/analytics/widgets",
                "/analytics/snapshots",
            ],
            "privacy":             [
                "/privacy/settings",
                "/privacy/policies",
                "/privacy/export-logs",
                "/privacy/super-admin-blocks",
            ],
            "support_access":      [
                "/support/request",
                "/support/approve",
                "/support/revoke",
                "/support/history",
            ],
            "security":            [                          # Phase 7 — NEW
                "/security/logins",
                "/security/mfa",
                "/security/tokens",
                "/security/sessions",
            ],
        },
    }


@app.get("/health", tags=["General"])
def health():
    return {
        "status":  "healthy",
        "service": "tower_ai_enterprise",
        "version": "5.7.0",
        "phase":   "phase7_security_operations_center",
    }


logger.info("Tower AI Enterprise Platform v5.7 — all routes registered.")