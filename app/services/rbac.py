"""
app/services/rbac.py
====================
Role-Based Access Control service.

Provides:
  - Permission checking by role
  - Role → Permission mapping (seeded at startup)
  - FastAPI dependencies for permission-based access control
  - require_permission() factory for endpoint-level checks

Role hierarchy:
    SUPER_ADMIN        → platform-level only. CANNOT access org data.
    ORG_ADMIN          → full control within their org
    DEPARTMENT_MANAGER → manage their department + view costs
    TEAM_LEAD          → manage their team members
    USER               → chat only, view own usage
"""

import uuid
from functools import lru_cache
from typing import Set

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models_enterprise import (
    Role, Permission, RolePermission, UserOrgRole, ROLE_NAMES, PERMISSION_NAMES
)
from app.services.auth import decode_token

bearer_scheme = HTTPBearer(auto_error=True)

# ── Role → Permissions mapping ────────────────────────────────────────────────
# Defines exactly which permissions each role receives.
# Super Admin explicitly EXCLUDED from org-data permissions.

ROLE_PERMISSION_MAP: dict[str, Set[str]] = {
    "SUPER_ADMIN": {
        "manage_organizations",
        "view_platform_metrics",
        "manage_providers",
        "manage_routing",
        # EXPLICITLY NOT included:
        # view_conversations, view_users (org data), manage_api_keys (org data)
    },
    "ORG_ADMIN": {
        "view_users",
        "create_users",
        "update_users",
        "delete_users",
        "view_costs",
        "view_usage",
        "export_reports",
        "manage_api_keys",
        "view_api_keys",
        "view_conversations",
        "delete_conversations",
        "manage_departments",
        "manage_teams",
        "manage_org_settings",
    },
    "DEPARTMENT_MANAGER": {
        "view_users",
        "view_costs",
        "view_usage",
        "export_reports",
        "view_conversations",
        "manage_teams",
    },
    "TEAM_LEAD": {
        "view_users",
        "view_usage",
        "view_conversations",
    },
    "USER": {
        "view_usage",   # own usage only
    },
}


# ── Seed functions ────────────────────────────────────────────────────────────

def seed_roles_and_permissions(db: Session) -> None:
    """
    Idempotent seed — creates roles, permissions, and role_permissions.
    Called at startup from main.py.
    """
    # Seed roles
    role_map: dict[str, Role] = {}
    for name in ROLE_NAMES:
        r = db.query(Role).filter(Role.name == name).first()
        if not r:
            r = Role(name=name, description=f"System role: {name}")
            db.add(r)
            db.flush()
        role_map[name] = r

    # Seed permissions
    perm_map: dict[str, Permission] = {}
    for name in PERMISSION_NAMES:
        p = db.query(Permission).filter(Permission.name == name).first()
        if not p:
            p = Permission(name=name, description=f"Permission: {name}")
            db.add(p)
            db.flush()
        perm_map[name] = p

    # Seed role_permissions
    for role_name, perm_names in ROLE_PERMISSION_MAP.items():
        role = role_map.get(role_name)
        if not role:
            continue
        for perm_name in perm_names:
            perm = perm_map.get(perm_name)
            if not perm:
                continue
            exists = db.query(RolePermission).filter(
                RolePermission.role_id == role.id,
                RolePermission.permission_id == perm.id,
            ).first()
            if not exists:
                db.add(RolePermission(role_id=role.id, permission_id=perm.id))

    db.commit()


# ── Permission resolution ─────────────────────────────────────────────────────

@lru_cache(maxsize=20)
def get_permissions_for_role(role_name: str) -> frozenset:
    """Return the set of permission names for a given role name (cached)."""
    return frozenset(ROLE_PERMISSION_MAP.get(role_name, set()))


def user_has_permission(
    db: Session,
    user_id: str,
    permission: str,
    org_id: str | None = None,
) -> bool:
    """
    Check whether a user has a specific permission.

    For SUPER_ADMIN: checks the global role (no org_id needed).
    For org-scoped roles: checks user_org_roles for the given org.
    Falls back to the legacy `role` column on the users table.
    """
    from app.db.models import User

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False

    # Check legacy role column first (backward compat)
    legacy_role = user.role.upper() if user.role else ""
    if legacy_role == "ADMIN":
        legacy_role = "ORG_ADMIN"
    if legacy_role == "EMPLOYEE":
        legacy_role = "USER"

    legacy_perms = get_permissions_for_role(legacy_role)
    if permission in legacy_perms:
        return True

    # Check enterprise user_org_roles
    if org_id:
        rows = (
            db.query(UserOrgRole, Role)
            .join(Role, Role.id == UserOrgRole.role_id)
            .filter(
                UserOrgRole.user_id == user_id,
                UserOrgRole.organization_id == org_id,
            )
            .all()
        )
        for ur, role in rows:
            if permission in get_permissions_for_role(role.name):
                return True

    return False


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def require_permission(permission: str, org_id_param: str | None = None):
    """
    Factory that returns a FastAPI dependency checking for a specific permission.

    Usage:
        @router.delete("/users/{user_id}")
        def delete_user(
            user_id: str,
            _: None = Depends(require_permission("delete_users")),
            db: Session = Depends(get_db),
        ):
            ...
    """
    def dependency(
        credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
        db: Session = Depends(get_db),
    ):
        payload = decode_token(credentials.credentials)
        user_id = payload.get("sub")
        org_id  = payload.get("org_id") or org_id_param

        if not user_has_permission(db, user_id, permission, org_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: '{permission}' required",
            )
        return payload

    return dependency


def require_super_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """
    Dependency: enforces SUPER_ADMIN role.
    Super admin CANNOT access org data — this is checked at the service level.
    """
    payload = decode_token(credentials.credentials)
    role = payload.get("role", "").upper()
    if role != "SUPER_ADMIN":
        raise HTTPException(403, "Super Admin access required")
    return payload


def require_org_admin(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> dict:
    """Dependency: enforces ORG_ADMIN or SUPER_ADMIN."""
    payload = decode_token(credentials.credentials)
    role = payload.get("role", "").upper()
    allowed = {"ORG_ADMIN", "SUPER_ADMIN", "ADMIN"}  # ADMIN = legacy
    if role not in allowed:
        raise HTTPException(403, "Organization Admin access required")
    return payload


def require_manager_or_above(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
) -> dict:
    """Dependency: enforces DEPARTMENT_MANAGER, ORG_ADMIN, or SUPER_ADMIN."""
    payload = decode_token(credentials.credentials)
    role = payload.get("role", "").upper()
    allowed = {"DEPARTMENT_MANAGER", "TEAM_LEAD", "ORG_ADMIN", "SUPER_ADMIN", "ADMIN", "MANAGER"}
    if role not in allowed:
        raise HTTPException(403, "Manager or above required")
    return payload
