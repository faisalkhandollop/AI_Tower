"""
api/alerts.py - Alerts System

Routes:
    GET  /alerts/                → List all alerts (admin)
    GET  /alerts/unread          → Unread alerts count
    POST /alerts/{alert_id}/read → Mark alert as read
    POST /alerts/read-all        → Mark all alerts as read
    DELETE /alerts/{alert_id}    → Delete alert
    POST /alerts/check           → Trigger alert evaluation (quota, budget, provider)
"""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db.database import get_db
from app.db.models import Alert, Team, User, UsageLog, UserTeam
from app.services.auth import require_admin

router = APIRouter(prefix="/alerts", tags=["Alerts"])

QUOTA_WARN_PCT  = 0.80
QUOTA_CRIT_PCT  = 0.90
BUDGET_CRIT_PCT = 0.90


def _create_alert(db: Session, alert_type: str, entity_type: str,
                  entity_id: str, entity_name: str, message: str):
    # Deduplicate — don't create same alert if unread one already exists
    existing = db.query(Alert).filter(
        Alert.alert_type  == alert_type,
        Alert.entity_id   == entity_id,
        Alert.is_read     == False,
    ).first()
    if existing:
        return  # already alerted
    db.add(Alert(alert_type=alert_type, entity_type=entity_type,
                 entity_id=entity_id, entity_name=entity_name, message=message))


def run_alert_checks(db: Session) -> list[dict]:
    fired = []

    # ── Team quota alerts ──────────────────────────────────────────────────────
    teams = db.query(Team).filter(Team.is_active == True).all()
    for team in teams:
        used = db.query(func.coalesce(func.sum(UsageLog.total_tokens), 0))\
            .join(UserTeam, UserTeam.user_id == UsageLog.user_id)\
            .filter(UserTeam.team_id == team.id).scalar() or 0
        pct = used / team.token_quota if team.token_quota else 0

        if pct >= QUOTA_CRIT_PCT:
            msg = (f"Team '{team.name}' has used {round(pct*100,1)}% of its token quota "
                   f"({used:,} / {team.token_quota:,} tokens).")
            _create_alert(db, "quota_90", "team", str(team.id), team.name, msg)
            fired.append({"type": "quota_90", "team": team.name, "pct": round(pct*100,1)})
        elif pct >= QUOTA_WARN_PCT:
            msg = (f"Team '{team.name}' has used {round(pct*100,1)}% of its token quota "
                   f"({used:,} / {team.token_quota:,} tokens).")
            _create_alert(db, "quota_80", "team", str(team.id), team.name, msg)
            fired.append({"type": "quota_80", "team": team.name, "pct": round(pct*100,1)})

        # Budget alerts
        cost = db.query(func.coalesce(func.sum(UsageLog.cost), 0))\
            .join(UserTeam, UserTeam.user_id == UsageLog.user_id)\
            .filter(UserTeam.team_id == team.id).scalar() or 0
        bpct = float(cost) / team.budget_usd if team.budget_usd else 0
        if bpct >= BUDGET_CRIT_PCT:
            msg = (f"Team '{team.name}' has used {round(bpct*100,1)}% of its budget "
                   f"(${float(cost):.2f} / ${team.budget_usd:.2f}).")
            _create_alert(db, "budget_90", "team", str(team.id), team.name, msg)
            fired.append({"type": "budget_90", "team": team.name, "budget_pct": round(bpct*100,1)})

    db.commit()
    return fired


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/", summary="List all alerts")
def list_alerts(
    unread_only: bool = Query(False),
    alert_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    q = db.query(Alert)
    if unread_only:
        q = q.filter(Alert.is_read == False)
    if alert_type:
        q = q.filter(Alert.alert_type == alert_type)
    alerts = q.order_by(Alert.created_at.desc()).limit(limit).all()
    return {
        "total": len(alerts),
        "alerts": [
            {"id": str(a.id), "alert_type": a.alert_type, "entity_type": a.entity_type,
             "entity_id": a.entity_id, "entity_name": a.entity_name,
             "message": a.message, "is_read": a.is_read, "created_at": str(a.created_at)}
            for a in alerts
        ]
    }


@router.get("/unread", summary="Get unread alert count")
def unread_count(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    count = db.query(Alert).filter(Alert.is_read == False).count()
    by_type = db.query(Alert.alert_type, func.count(Alert.id))\
        .filter(Alert.is_read == False).group_by(Alert.alert_type).all()
    return {"unread_count": count, "by_type": {t: c for t, c in by_type}}


@router.post("/{alert_id}/read", summary="Mark alert as read")
def mark_read(alert_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if not a:
        from fastapi import HTTPException
        raise HTTPException(404, "Alert not found")
    a.is_read = True; db.commit()
    return {"message": "Alert marked as read"}


@router.post("/read-all", summary="Mark all alerts as read")
def mark_all_read(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    count = db.query(Alert).filter(Alert.is_read == False).update({"is_read": True})
    db.commit()
    return {"message": f"{count} alerts marked as read"}


@router.delete("/{alert_id}", summary="Delete an alert")
def delete_alert(alert_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    a = db.query(Alert).filter(Alert.id == alert_id).first()
    if not a:
        from fastapi import HTTPException
        raise HTTPException(404, "Alert not found")
    db.delete(a); db.commit()
    return {"message": "Alert deleted"}


@router.post("/check", summary="Trigger alert evaluation (quota, budget)")
def trigger_check(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    fired = run_alert_checks(db)
    return {
        "message":   "Alert check completed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "alerts_fired": len(fired),
        "details": fired,
    }
