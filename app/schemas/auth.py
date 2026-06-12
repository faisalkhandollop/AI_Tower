"""
schemas/auth.py - Auth request/response schemas.
"""

from pydantic import BaseModel, EmailStr, Field
from typing import Optional


class LoginRequest(BaseModel):
    email:    EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    user_id:      str
    name:         str
    role:         str


class RegisterRequest(BaseModel):
    name:       str     = Field(..., min_length=1, max_length=100)
    email:      EmailStr
    password:   str     = Field(..., min_length=8, max_length=128)
    department: Optional[str] = Field(None, max_length=100)
    role:       str     = Field("employee", pattern="^(employee|admin)$")
    token_quota: int    = Field(100_000, ge=1000, le=10_000_000)


class UserResponse(BaseModel):
    id:          str
    name:        str
    email:       str
    department:  Optional[str]
    role:        str
    is_active:   bool
    token_quota: int
    created_at:  str


class UserUpdateRequest(BaseModel):
    name:        Optional[str]  = Field(None, min_length=1, max_length=100)
    department:  Optional[str]  = Field(None, max_length=100)
    role:        Optional[str]  = Field(None, pattern="^(employee|admin)$")
    is_active:   Optional[bool] = None
    token_quota: Optional[int]  = Field(None, ge=1000, le=10_000_000)
