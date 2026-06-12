"""
api/auth.py - Authentication endpoints.

Routes:
    POST /auth/register  → create a new user (admin only)
    POST /auth/login     → exchange credentials for JWT
    GET  /auth/me        → return current user profile
    POST /auth/logout    → client-side token discard (documented pattern)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth import (
    create_access_token,
    hash_password,
    verify_password,
    get_current_user,
    require_admin,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


# ── POST /auth/register ────────────────────────────────────────────────────────
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user (admin only)",
)
def register(
    payload: RegisterRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """
    Create a new user account.
    Only admins can register new users.
    """
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{payload.email}' is already registered",
        )

    user = User(
        name          = payload.name,
        email         = payload.email,
        password_hash = hash_password(payload.password),
        department    = payload.department,
        role          = payload.role,
        token_quota   = payload.token_quota,
        is_active     = True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

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


# ── POST /auth/login ───────────────────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse, summary="Login — get JWT token")
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """
    Authenticate with email + password.
    Returns a signed JWT access token.
    """
    user = db.query(User).filter(User.email == payload.email).first()

    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact your administrator.",
        )

    # Stamp last_login
    user.last_login = datetime.now(timezone.utc)
    db.commit()

    token = create_access_token(data={"sub": str(user.id), "role": user.role})

    return TokenResponse(
        access_token = token,
        token_type   = "bearer",
        user_id      = str(user.id),
        name         = user.name,
        role         = user.role,
    )


# ── GET /auth/me ───────────────────────────────────────────────────────────────
@router.get("/me", response_model=UserResponse, summary="Get current user profile")
def me(current_user: User = Depends(get_current_user)):
    """Return the profile of the currently authenticated user."""
    return UserResponse(
        id          = str(current_user.id),
        name        = current_user.name,
        email       = current_user.email,
        department  = current_user.department,
        role        = current_user.role,
        is_active   = current_user.is_active,
        token_quota = current_user.token_quota,
        created_at  = str(current_user.created_at),
    )


# ── POST /auth/logout ──────────────────────────────────────────────────────────
@router.post("/logout", summary="Logout (client-side token discard)")
def logout(_current_user: User = Depends(get_current_user)):
    """
    Stateless JWT logout — instructs the client to discard its token.
    For server-side revocation, implement a token blacklist (Redis recommended).
    """
    return {"message": "Logged out successfully. Discard your token on the client."}
