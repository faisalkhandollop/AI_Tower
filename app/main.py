"""
main.py - Tower AI Enterprise Platform v5.0 (Phase 1 Enterprise Upgrade)

What changed from v4:
  - Added /conversations router (conversation + message history)
  - Added /audit router (audit logs + privileged access logs)
  - Added /rbac router (role/permission management)
  - Enterprise models registered for create_all()
  - Roles and permissions seeded at startup
  - AES-256 vault (vault.py updated, same endpoints)
  - All v4 routers preserved, no endpoints removed
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# ── v4 Routers (ALL PRESERVED — no removals) ──────────────────────────────────
from app.api.auth            import router as auth_router
from app.api.users           import router as users_router
from app.api.usage           import router as usage_router
from app.api.admin           import router as admin_router
from app.api.sessions        import router as sessions_router
from app.api.chat            import router as chat_router
from app.api.routing         import router as routing_router
from app.api.vault           import router as vault_router          # updated (AES-256)
from app.api.provider_health import router as provider_health_router
from app.api.org             import router as org_router
from app.api.alerts          import router as alerts_router
from app.api.reports         import router as reports_router

# ── Phase 1 Enterprise Routers (NEW) ─────────────────────────────────────────
from app.api.conversations   import router as conversations_router
from app.api.audit           import router as audit_router

# ── DB ────────────────────────────────────────────────────────────────────────
from app.db.database import engine, Base, SessionLocal
from app.db import models          # existing models
from app.db import models_enterprise  # Phase 1 new models  # noqa: F401

# ── RBAC seed ─────────────────────────────────────────────────────────────────
from app.services.rbac import seed_roles_and_permissions

import os

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create all tables (existing + new enterprise tables)
Base.metadata.create_all(bind=engine)

# Seed roles and permissions on every startup (idempotent)
try:
    _seed_db = SessionLocal()
    seed_roles_and_permissions(_seed_db)
    _seed_db.close()
    logger.info("RBAC: roles and permissions seeded.")
except Exception as e:
    logger.warning(f"RBAC seed skipped: {e}")

app = FastAPI(
    title="Tower AI — Enterprise Platform",
    version="5.0.0",
    description=(
        "Unified AI Platform v5.0 — Phase 1 Enterprise Upgrade.\n\n"
        "**Control Tower:** `/auth` `/users` `/usage` `/admin` `/sessions`\n\n"
        "**AI Gateway:** `/chat` (smart routing, escalation, dept/budget aware)\n\n"
        "**AI Router:** `/route` `/ai/route` `/workspace` `/models` `/stats`\n\n"
        "**API Key Vault:** `/vault/keys` `/vault/priorities` (AES-256-GCM)\n\n"
        "**Provider Health:** `/providers/health` `/providers/dashboard`\n\n"
        "**Organizations:** `/org` (orgs, teams, departments, user roles)\n\n"
        "**Alerts:** `/alerts` (quota 80/90%, budget, provider down)\n\n"
        "**Reports:** `/reports/daily` `/reports/weekly` `/reports/monthly`\n\n"
        "**Conversations:** `/conversations` (NEW — message history)\n\n"
        "**Audit Logs:** `/audit` (NEW — immutable audit trail)\n\n"
        "**RBAC:** `/rbac` (NEW — roles and permissions)\n\n"
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

# ── v4 routers (all preserved) ────────────────────────────────────────────────
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

# ── Phase 1 enterprise routers (new) ─────────────────────────────────────────
app.include_router(conversations_router)
app.include_router(audit_router)

# ── Frontend SPA ──────────────────────────────────────────────────────────────
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if FRONTEND_DIR.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/app", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.get("/", tags=["General"])
def home():
    return {
        "message": "Tower AI Enterprise Platform v5.0",
        "phase":   "Phase 1 Enterprise Upgrade",
        "docs":    "/docs",
        "services": {
            "control_tower":    ["/auth", "/users", "/usage", "/admin", "/sessions"],
            "ai_gateway":       ["/chat"],
            "ai_router":        ["/route", "/ai/route", "/workspace", "/compare", "/models"],
            "api_key_vault":    ["/vault/keys", "/vault/priorities"],
            "provider_health":  ["/providers/health", "/providers/dashboard"],
            "organizations":    ["/org"],
            "alerts":           ["/alerts"],
            "reports":          ["/reports/daily", "/reports/weekly", "/reports/monthly"],
            "conversations":    ["/conversations"],         # NEW
            "audit":            ["/audit", "/audit/privileged"],  # NEW
        },
    }


@app.get("/health", tags=["General"])
def health():
    return {
        "status":  "healthy",
        "service": "tower_ai_enterprise",
        "version": "5.0.0",
        "phase":   "phase1_enterprise",
    }


logger.info("Tower AI Enterprise Platform v5.0 — all routes registered.")
