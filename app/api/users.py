"""
api/users.py - User management CRUD endpoints.

Routes:
    GET    /users           → list all users (admin only)
    GET    /users/{user_id} → get single user (admin only)
    PUT    /users/{user_id} → update user (admin only)
    DELETE /users/{user_id} → deactivate user (admin only, soft-delete)
"""

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.schemas.auth import UserResponse, UserUpdateRequest
from app.services.auth import require_admin, hash_password

router = APIRouter(prefix="/users", tags=["User Management"])


def _to_response(user: User) -> UserResponse:
    return UserResponse(
        id          = str(user.id),
        name        = user.name,
        email       = user.email,
        department  = user.department,
        role        = user.role,
        is_active   = user.is_active,
        token_quota = user.token_quota,
        created_at  = str(user.created_at),
    )


# ── GET /users ─────────────────────────────────────────────────────────────────
@router.get(
    "/",
    response_model=list[UserResponse],
    summary="List all users (admin only)",
)
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Return all registered users ordered by creation date."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_to_response(u) for u in users]


# ── GET /users/{user_id} ───────────────────────────────────────────────────────
@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="Get a single user (admin only)",
)
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")

    return _to_response(user)


# ── PUT /users/{user_id} ───────────────────────────────────────────────────────
@router.put(
    "/{user_id}",
    response_model=UserResponse,
    summary="Update user fields (admin only)",
)
def update_user(
    user_id: str,
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Partially update a user. Only provided fields are changed.
    Admins can update: name, department, role, is_active, token_quota.
    """
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    db.commit()
    db.refresh(user)
    return _to_response(user)


# ── DELETE /users/{user_id} ────────────────────────────────────────────────────
@router.delete(
    "/{user_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate a user (soft delete, admin only)",
)
def deactivate_user(
    user_id: str,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Soft-delete: sets is_active=False.
    The user record and all their usage logs are preserved for reporting.
    """
    try:
        uid = _uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id format")

    user = db.query(User).filter(User.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail=f"User '{user_id}' not found")

    if not user.is_active:
        return {"message": f"User '{user.email}' is already deactivated"}

    user.is_active = False
    db.commit()
    return {"message": f"User '{user.email}' has been deactivated"}
