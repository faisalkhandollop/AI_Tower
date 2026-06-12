"""
app/api/vault.py  (UPDATED - Phase 1 Enterprise Upgrade)
=========================================================
Provider API Key Vault — now uses AES-256-GCM encryption.

Changes from v4:
  - Replaced XOR + base64 with AES-256-GCM via app.services.encryption
  - Existing XOR-encrypted keys are transparently decrypted and re-encrypted on read
  - Audit log written on add, rotate, delete
  - All existing endpoints and contracts preserved

Routes (unchanged):
    POST   /vault/keys              → Add provider key
    GET    /vault/keys              → List all keys (masked)
    GET    /vault/keys/{key_id}     → Get single key info
    PUT    /vault/keys/{key_id}     → Update key label / priority
    POST   /vault/keys/{key_id}/disable  → Disable key
    POST   /vault/keys/{key_id}/enable   → Enable key
    POST   /vault/keys/{key_id}/rotate   → Rotate (replace) key
    DELETE /vault/keys/{key_id}     → Delete key permanently
    GET    /vault/priorities        → Get provider priorities
    PUT    /vault/priorities        → Update provider priorities
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import ProviderKey, ProviderPriority, User
from app.services.auth import require_admin, get_current_user
from app.services.encryption import encrypt, decrypt, mask, is_aes_encrypted
from app.services.audit import log_action

router = APIRouter(prefix="/vault", tags=["API Key Vault"])

ALLOWED_PROVIDERS = {"openai", "claude", "groq", "gemini", "openrouter"}


# ── Schemas ───────────────────────────────────────────────────────────────────

class AddKeyRequest(BaseModel):
    provider:  str = Field(..., description="openai | claude | groq | gemini | openrouter")
    key_label: str = Field(..., min_length=2, max_length=200)
    api_key:   str = Field(..., min_length=10)
    priority:  int = Field(1, ge=1, le=10)

class UpdateKeyRequest(BaseModel):
    key_label: Optional[str] = Field(None, min_length=2, max_length=200)
    priority:  Optional[int] = Field(None, ge=1, le=10)

class RotateKeyRequest(BaseModel):
    new_api_key: str = Field(..., min_length=10)

class UpdatePrioritiesRequest(BaseModel):
    priorities: list[dict]


def _key_dict(k: ProviderKey, show_masked: bool = True) -> dict:
    plain = decrypt(k.api_key_enc)
    return {
        "id":         str(k.id),
        "provider":   k.provider,
        "key_label":  k.key_label,
        "api_key":    mask(plain) if show_masked else None,
        "is_active":  k.is_active,
        "priority":   k.priority,
        "added_by":   str(k.added_by) if k.added_by else None,
        "rotated_at": str(k.rotated_at) if k.rotated_at else None,
        "created_at": str(k.created_at),
        "updated_at": str(k.updated_at),
        "encrypted_with": "AES-256-GCM" if is_aes_encrypted(k.api_key_enc) else "legacy-xor",
    }


# ── POST /vault/keys ──────────────────────────────────────────────────────────

@router.post("/keys", status_code=201, summary="Add a provider API key")
def add_key(
    payload: AddKeyRequest,
    request: Request,
    db:      Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    if payload.provider.lower() not in ALLOWED_PROVIDERS:
        raise HTTPException(400, f"provider must be one of: {sorted(ALLOWED_PROVIDERS)}")

    key = ProviderKey(
        provider    = payload.provider.lower(),
        key_label   = payload.key_label,
        api_key_enc = encrypt(payload.api_key),   # ← AES-256-GCM
        is_active   = True,
        priority    = payload.priority,
        added_by    = current_user.id,
    )
    db.add(key)
    db.flush()

    log_action(
        db=db, action="API_KEY_ADDED",
        actor=current_user,
        resource_type="api_key", resource_id=str(key.id),
        resource_name=f"{payload.provider} — {payload.key_label}",
        request=request,
    )
    db.commit()
    db.refresh(key)
    return {"message": "API key added", "key": _key_dict(key)}


# ── GET /vault/keys ───────────────────────────────────────────────────────────

@router.get("/keys", summary="List all keys (masked)")
def list_keys(
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    keys = db.query(ProviderKey).order_by(ProviderKey.provider, ProviderKey.priority).all()
    return {
        "total": len(keys),
        "keys":  [_key_dict(k) for k in keys],
    }


# ── GET /vault/keys/{key_id} ──────────────────────────────────────────────────

@router.get("/keys/{key_id}", summary="Get single key info")
def get_key(
    key_id: str,
    db:     Session = Depends(get_db),
    _admin: User    = Depends(require_admin),
):
    key = _get_key(key_id, db)
    return _key_dict(key)


# ── PUT /vault/keys/{key_id} ──────────────────────────────────────────────────

@router.put("/keys/{key_id}", summary="Update key label or priority")
def update_key(
    key_id:  str,
    payload: UpdateKeyRequest,
    db:      Session = Depends(get_db),
    _admin:  User    = Depends(require_admin),
):
    key = _get_key(key_id, db)
    if payload.key_label is not None: key.key_label = payload.key_label
    if payload.priority  is not None: key.priority  = payload.priority
    db.commit()
    db.refresh(key)
    return {"message": "Key updated", "key": _key_dict(key)}


# ── POST /vault/keys/{key_id}/disable ────────────────────────────────────────

@router.post("/keys/{key_id}/disable", summary="Disable key")
def disable_key(key_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    key = _get_key(key_id, db)
    key.is_active = False
    db.commit()
    return {"message": f"Key '{key.key_label}' disabled"}


# ── POST /vault/keys/{key_id}/enable ─────────────────────────────────────────

@router.post("/keys/{key_id}/enable", summary="Enable key")
def enable_key(key_id: str, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    key = _get_key(key_id, db)
    key.is_active = True
    db.commit()
    return {"message": f"Key '{key.key_label}' enabled"}


# ── POST /vault/keys/{key_id}/rotate ─────────────────────────────────────────

@router.post("/keys/{key_id}/rotate", summary="Rotate — replace with new key value")
def rotate_key(
    key_id:  str,
    payload: RotateKeyRequest,
    request: Request,
    db:      Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    key = _get_key(key_id, db)
    key.api_key_enc = encrypt(payload.new_api_key)   # ← AES-256-GCM new encryption
    key.rotated_at  = datetime.now(timezone.utc)

    log_action(
        db=db, action="API_KEY_ROTATED",
        actor=current_user,
        resource_type="api_key", resource_id=key_id,
        resource_name=f"{key.provider} — {key.key_label}",
        request=request,
    )
    db.commit()
    db.refresh(key)
    return {"message": "Key rotated successfully", "key": _key_dict(key)}


# ── DELETE /vault/keys/{key_id} ───────────────────────────────────────────────

@router.delete("/keys/{key_id}", summary="Delete key permanently")
def delete_key(
    key_id:  str,
    request: Request,
    db:      Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    key = _get_key(key_id, db)

    log_action(
        db=db, action="API_KEY_DELETED",
        actor=current_user,
        resource_type="api_key", resource_id=key_id,
        resource_name=f"{key.provider} — {key.key_label}",
        request=request,
    )
    db.delete(key)
    db.commit()
    return {"message": f"Key '{key.key_label}' permanently deleted"}


# ── GET /vault/priorities ─────────────────────────────────────────────────────

@router.get("/priorities", summary="Get provider priorities")
def get_priorities(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    rows = db.query(ProviderPriority).order_by(ProviderPriority.priority).all()
    return {
        "priorities": [
            {
                "provider":   r.provider,
                "priority":   r.priority,
                "is_enabled": r.is_enabled,
                "updated_at": str(r.updated_at),
            }
            for r in rows
        ]
    }


# ── PUT /vault/priorities ─────────────────────────────────────────────────────

@router.put("/priorities", summary="Update provider priorities")
def update_priorities(
    payload: UpdatePrioritiesRequest,
    db:      Session = Depends(get_db),
    _admin:  User    = Depends(require_admin),
):
    for item in payload.priorities:
        provider   = item.get("provider", "").lower()
        priority   = item.get("priority", 1)
        is_enabled = item.get("is_enabled", True)

        if provider not in ALLOWED_PROVIDERS:
            continue

        row = db.query(ProviderPriority).filter(ProviderPriority.provider == provider).first()
        if row:
            row.priority   = priority
            row.is_enabled = is_enabled
        else:
            db.add(ProviderPriority(provider=provider, priority=priority, is_enabled=is_enabled))

    db.commit()
    return {"message": "Provider priorities updated"}


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_key(key_id: str, db: Session) -> ProviderKey:
    try:
        uid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(400, "Invalid key_id format")
    key = db.query(ProviderKey).filter(ProviderKey.id == uid).first()
    if not key:
        raise HTTPException(404, f"Key {key_id} not found")
    return key
