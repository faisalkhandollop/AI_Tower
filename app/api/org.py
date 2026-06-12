"""
api/org.py - Organization, Teams, Departments, Roles

Routes:
    POST /org/                      → Create organization
    GET  /org/                      → List all organizations
    GET  /org/{org_id}              → Get organization
    PUT  /org/{org_id}              → Update organization
    DELETE /org/{org_id}            → Delete organization

    POST /org/{org_id}/teams        → Create team
    GET  /org/{org_id}/teams        → List teams in org
    GET  /org/teams/{team_id}       → Get team
    PUT  /org/teams/{team_id}       → Update team
    DELETE /org/teams/{team_id}     → Delete team
    POST /org/teams/{team_id}/members → Add user to team
    DELETE /org/teams/{team_id}/members/{user_id} → Remove user from team
    GET  /org/teams/{team_id}/members → List team members

    POST /org/{org_id}/departments  → Create department
    GET  /org/{org_id}/departments  → List departments
    PUT  /org/departments/{dept_id} → Update department routing policy
    DELETE /org/departments/{dept_id} → Delete department
"""

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Organization, Team, Department, UserTeam, User, UsageLog
from app.services.auth import require_admin, get_current_user
from sqlalchemy import func

router = APIRouter(prefix="/org", tags=["Organization Management"])

VALID_ROLES = {"super_admin", "admin", "manager", "employee"}
ROUTING_POLICIES = {"cost_aware", "quality_first", "free_first", "department_preferred"}
ALLOWED_PROVIDERS = {"openai", "claude", "groq", "gemini", "openrouter"}


# ── Schemas ────────────────────────────────────────────────────────────────────
class CreateOrgRequest(BaseModel):
    name:        str = Field(..., min_length=2, max_length=200)
    description: Optional[str] = None

class UpdateOrgRequest(BaseModel):
    name:        Optional[str] = Field(None, min_length=2, max_length=200)
    description: Optional[str] = None
    is_active:   Optional[bool] = None

class CreateTeamRequest(BaseModel):
    name:        str   = Field(..., min_length=2, max_length=200)
    department:  Optional[str] = None
    token_quota: int   = Field(500_000, ge=10_000)
    budget_usd:  float = Field(100.0,   ge=0)

class UpdateTeamRequest(BaseModel):
    name:        Optional[str]   = None
    department:  Optional[str]   = None
    token_quota: Optional[int]   = Field(None, ge=10_000)
    budget_usd:  Optional[float] = Field(None, ge=0)
    is_active:   Optional[bool]  = None

class CreateDeptRequest(BaseModel):
    name:               str         = Field(..., min_length=2, max_length=200)
    allowed_providers:  list[str]   = Field(default_factory=list)
    preferred_provider: Optional[str] = None
    routing_policy:     str         = Field("cost_aware")

class UpdateDeptRequest(BaseModel):
    allowed_providers:  Optional[list[str]] = None
    preferred_provider: Optional[str]       = None
    routing_policy:     Optional[str]       = None

class AddMemberRequest(BaseModel):
    user_id: str
    role:    str = Field("employee")


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _org_dict(o: Organization) -> dict:
    return {"id": str(o.id), "name": o.name, "slug": o.slug,
            "description": o.description, "is_active": o.is_active,
            "created_at": str(o.created_at)}

def _team_dict(t: Team) -> dict:
    return {"id": str(t.id), "org_id": str(t.org_id), "name": t.name,
            "department": t.department, "token_quota": t.token_quota,
            "budget_usd": t.budget_usd, "is_active": t.is_active,
            "created_at": str(t.created_at)}

def _dept_dict(d: Department) -> dict:
    return {"id": str(d.id), "org_id": str(d.org_id), "name": d.name,
            "allowed_providers": d.allowed_providers,
            "preferred_provider": d.preferred_provider,
            "routing_policy": d.routing_policy,
            "created_at": str(d.created_at)}


# ══════════════════════════════════════════════════════════════════════════════
# ORGANIZATION CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/", status_code=201, summary="Create organization")
def create_org(payload: CreateOrgRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    slug = _slugify(payload.name)
    if db.query(Organization).filter(Organization.slug == slug).first():
        slug = slug + "-" + str(uuid.uuid4())[:8]
    org = Organization(name=payload.name, slug=slug, description=payload.description)
    db.add(org); db.commit(); db.refresh(org)
    return {"message": "Organization created", "org": _org_dict(org)}

@router.get("/", summary="List all organizations")
def list_orgs(db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    orgs = db.query(Organization).order_by(Organization.name).all()
    return {"total": len(orgs), "organizations": [_org_dict(o) for o in orgs]}

@router.get("/{org_id}", summary="Get organization")
def get_org(org_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(404, "Organization not found")
    teams = db.query(Team).filter(Team.org_id == org_id).count()
    depts = db.query(Department).filter(Department.org_id == org_id).count()
    return {**_org_dict(org), "team_count": teams, "dept_count": depts}

@router.put("/{org_id}", summary="Update organization")
def update_org(org_id: str, payload: UpdateOrgRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(404, "Organization not found")
    if payload.name is not None:        org.name = payload.name
    if payload.description is not None: org.description = payload.description
    if payload.is_active is not None:   org.is_active = payload.is_active
    db.commit(); db.refresh(org)
    return {"message": "Updated", "org": _org_dict(org)}

@router.delete("/{org_id}", summary="Delete organization")
def delete_org(org_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    org = db.query(Organization).filter(Organization.id == org_id).first()
    if not org: raise HTTPException(404, "Organization not found")
    db.delete(org); db.commit()
    return {"message": "Organization deleted"}


# ══════════════════════════════════════════════════════════════════════════════
# TEAM CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{org_id}/teams", status_code=201, summary="Create team in org")
def create_team(org_id: str, payload: CreateTeamRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    if not db.query(Organization).filter(Organization.id == org_id).first():
        raise HTTPException(404, "Organization not found")
    team = Team(org_id=org_id, name=payload.name, department=payload.department,
                token_quota=payload.token_quota, budget_usd=payload.budget_usd)
    db.add(team); db.commit(); db.refresh(team)
    return {"message": "Team created", "team": _team_dict(team)}

@router.get("/{org_id}/teams", summary="List teams in organization")
def list_teams(org_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    teams = db.query(Team).filter(Team.org_id == org_id).order_by(Team.name).all()
    result = []
    for t in teams:
        member_count = db.query(UserTeam).filter(UserTeam.team_id == t.id).count()
        used_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
            .join(UserTeam, UserTeam.user_id == UsageLog.user_id)\
            .filter(UserTeam.team_id == t.id).scalar() or 0
        result.append({**_team_dict(t), "member_count": member_count,
                       "tokens_used": used_tokens,
                       "quota_pct": round(used_tokens / t.token_quota * 100, 1) if t.token_quota else 0})
    return {"total": len(result), "teams": result}

@router.get("/teams/{team_id}", summary="Get team details")
def get_team(team_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    t = db.query(Team).filter(Team.id == team_id).first()
    if not t: raise HTTPException(404, "Team not found")
    members = db.query(UserTeam).filter(UserTeam.team_id == team_id).all()
    used_tokens = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
        .join(UserTeam, UserTeam.user_id == UsageLog.user_id)\
        .filter(UserTeam.team_id == team_id).scalar() or 0
    used_cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0))\
        .join(UserTeam, UserTeam.user_id == UsageLog.user_id)\
        .filter(UserTeam.team_id == team_id).scalar() or 0
    return {
        **_team_dict(t),
        "member_count": len(members),
        "tokens_used":  used_tokens,
        "cost_used_usd": round(float(used_cost), 4),
        "quota_pct":    round(used_tokens / t.token_quota * 100, 1) if t.token_quota else 0,
        "budget_pct":   round(float(used_cost) / t.budget_usd * 100, 1) if t.budget_usd else 0,
    }

@router.put("/teams/{team_id}", summary="Update team")
def update_team(team_id: str, payload: UpdateTeamRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    t = db.query(Team).filter(Team.id == team_id).first()
    if not t: raise HTTPException(404, "Team not found")
    if payload.name is not None:        t.name = payload.name
    if payload.department is not None:  t.department = payload.department
    if payload.token_quota is not None: t.token_quota = payload.token_quota
    if payload.budget_usd is not None:  t.budget_usd = payload.budget_usd
    if payload.is_active is not None:   t.is_active = payload.is_active
    db.commit(); db.refresh(t)
    return {"message": "Team updated", "team": _team_dict(t)}

@router.delete("/teams/{team_id}", summary="Delete team")
def delete_team(team_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    t = db.query(Team).filter(Team.id == team_id).first()
    if not t: raise HTTPException(404, "Team not found")
    db.delete(t); db.commit()
    return {"message": "Team deleted"}

@router.post("/teams/{team_id}/members", status_code=201, summary="Add user to team")
def add_member(team_id: str, payload: AddMemberRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    t = db.query(Team).filter(Team.id == team_id).first()
    if not t: raise HTTPException(404, "Team not found")
    u = db.query(User).filter(User.id == payload.user_id).first()
    if not u: raise HTTPException(404, "User not found")
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"role must be one of: {sorted(VALID_ROLES)}")
    existing = db.query(UserTeam).filter(UserTeam.user_id == payload.user_id, UserTeam.team_id == team_id).first()
    if existing:
        existing.role = payload.role
    else:
        db.add(UserTeam(user_id=payload.user_id, team_id=team_id, org_id=t.org_id, role=payload.role))
    db.commit()
    return {"message": f"User '{u.name}' added to team '{t.name}' as {payload.role}"}

@router.delete("/teams/{team_id}/members/{user_id}", summary="Remove user from team")
def remove_member(team_id: str, user_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    ut = db.query(UserTeam).filter(UserTeam.team_id == team_id, UserTeam.user_id == user_id).first()
    if not ut: raise HTTPException(404, "Member not found in team")
    db.delete(ut); db.commit()
    return {"message": "Member removed from team"}

@router.get("/teams/{team_id}/members", summary="List team members")
def list_members(team_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    rows = db.query(UserTeam, User)\
        .join(User, User.id == UserTeam.user_id)\
        .filter(UserTeam.team_id == team_id).all()
    return {
        "team_id": team_id,
        "total": len(rows),
        "members": [
            {"user_id": str(ut.user_id), "name": u.name, "email": u.email,
             "role": ut.role, "department": u.department, "joined_at": str(ut.joined_at)}
            for ut, u in rows
        ]
    }


# ══════════════════════════════════════════════════════════════════════════════
# DEPARTMENT CRUD
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/{org_id}/departments", status_code=201, summary="Create department")
def create_dept(org_id: str, payload: CreateDeptRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    if not db.query(Organization).filter(Organization.id == org_id).first():
        raise HTTPException(404, "Organization not found")
    if payload.routing_policy not in ROUTING_POLICIES:
        raise HTTPException(400, f"routing_policy must be one of: {sorted(ROUTING_POLICIES)}")
    bad = [p for p in payload.allowed_providers if p not in ALLOWED_PROVIDERS]
    if bad: raise HTTPException(400, f"Unknown providers: {bad}")
    dept = Department(org_id=org_id, name=payload.name,
                      allowed_providers=payload.allowed_providers,
                      preferred_provider=payload.preferred_provider,
                      routing_policy=payload.routing_policy)
    db.add(dept); db.commit(); db.refresh(dept)
    return {"message": "Department created", "department": _dept_dict(dept)}

@router.get("/{org_id}/departments", summary="List departments in org")
def list_depts(org_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    depts = db.query(Department).filter(Department.org_id == org_id).all()
    return {"total": len(depts), "departments": [_dept_dict(d) for d in depts]}

@router.put("/departments/{dept_id}", summary="Update department routing policy")
def update_dept(dept_id: str, payload: UpdateDeptRequest, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    d = db.query(Department).filter(Department.id == dept_id).first()
    if not d: raise HTTPException(404, "Department not found")
    if payload.allowed_providers is not None:
        bad = [p for p in payload.allowed_providers if p not in ALLOWED_PROVIDERS]
        if bad: raise HTTPException(400, f"Unknown providers: {bad}")
        d.allowed_providers = payload.allowed_providers
    if payload.preferred_provider is not None: d.preferred_provider = payload.preferred_provider
    if payload.routing_policy is not None:
        if payload.routing_policy not in ROUTING_POLICIES:
            raise HTTPException(400, f"routing_policy must be one of: {sorted(ROUTING_POLICIES)}")
        d.routing_policy = payload.routing_policy
    db.commit(); db.refresh(d)
    return {"message": "Department updated", "department": _dept_dict(d)}

@router.delete("/departments/{dept_id}", summary="Delete department")
def delete_dept(dept_id: str, db: Session = Depends(get_db), _a: User = Depends(require_admin)):
    d = db.query(Department).filter(Department.id == dept_id).first()
    if not d: raise HTTPException(404, "Department not found")
    db.delete(d); db.commit()
    return {"message": "Department deleted"}
