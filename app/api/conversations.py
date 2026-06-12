"""
app/api/conversations.py
=========================
Conversation and message management endpoints.

Does NOT remove existing session functionality.
Conversations link to sessions via session_id FK.

Routes:
    POST   /conversations/                          → Create conversation
    GET    /conversations/                          → List user's conversations
    GET    /conversations/{conv_id}                 → Get conversation + messages
    DELETE /conversations/{conv_id}                 → Archive conversation
    GET    /conversations/{conv_id}/messages        → List messages
    POST   /conversations/{conv_id}/messages        → Add message to conversation
    DELETE /conversations/{conv_id}/messages/{msg_id} → Delete message
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import User
from app.db.models_enterprise import Conversation, Message
from app.services.auth import get_current_user, require_admin
from app.services.audit import log_action
from app.services.rbac import require_permission

router = APIRouter(prefix="/conversations", tags=["Conversations"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CreateConversationRequest(BaseModel):
    title:      Optional[str] = Field(None, max_length=500)
    department: Optional[str] = None
    team_id:    Optional[str] = None
    session_id: Optional[str] = None
    org_id:     str           = Field(..., description="Organization UUID")

class AddMessageRequest(BaseModel):
    role:       str  = Field(..., pattern="^(user|assistant|system)$")
    content:    str  = Field(..., min_length=1)
    token_count: Optional[int]   = None
    provider:   Optional[str]    = None
    model:      Optional[str]    = None
    cost:       Optional[float]  = None


def _conv_dict(c: Conversation) -> dict:
    return {
        "id":              str(c.id),
        "organization_id": str(c.organization_id),
        "user_id":         str(c.user_id),
        "session_id":      c.session_id,
        "title":           c.title,
        "department":      c.department,
        "team_id":         str(c.team_id) if c.team_id else None,
        "is_archived":     c.is_archived,
        "created_at":      str(c.created_at),
        "updated_at":      str(c.updated_at),
    }


def _msg_dict(m: Message) -> dict:
    return {
        "id":              str(m.id),
        "conversation_id": str(m.conversation_id),
        "role":            m.role,
        "content":         m.content,
        "token_count":     m.token_count,
        "provider":        m.provider,
        "model":           m.model,
        "cost":            float(m.cost) if m.cost else None,
        "created_at":      str(m.created_at),
    }


# ── POST /conversations/ ──────────────────────────────────────────────────────

@router.post("/", status_code=201, summary="Create a new conversation")
def create_conversation(
    payload: CreateConversationRequest,
    request: Request,
    db:      Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = Conversation(
        organization_id = uuid.UUID(payload.org_id),
        user_id         = current_user.id,
        session_id      = payload.session_id,
        title           = payload.title or "New Conversation",
        department      = payload.department,
        team_id         = uuid.UUID(payload.team_id) if payload.team_id else None,
    )
    db.add(conv)
    db.flush()

    log_action(
        db=db, action="CONVERSATION_DELETED",   # reuse — no CREATE action defined, log as info
        actor=current_user, org_id=payload.org_id,
        resource_type="conversation", resource_id=str(conv.id),
    )
    db.commit()
    db.refresh(conv)
    return {"conversation": _conv_dict(conv)}


# ── GET /conversations/ ───────────────────────────────────────────────────────

@router.get("/", summary="List conversations for current user")
def list_conversations(
    org_id:   str,
    archived: bool = False,
    limit:    int  = 20,
    offset:   int  = 0,
    db:       Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(Conversation).filter(
        Conversation.organization_id == uuid.UUID(org_id),
        Conversation.user_id         == current_user.id,
        Conversation.is_archived     == archived,
    ).order_by(Conversation.updated_at.desc())

    total = q.count()
    convs = q.limit(limit).offset(offset).all()

    return {
        "total":         total,
        "conversations": [_conv_dict(c) for c in convs],
    }


# ── GET /conversations/{conv_id} ──────────────────────────────────────────────

@router.get("/{conv_id}", summary="Get conversation with messages")
def get_conversation(
    conv_id: str,
    db:      Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = _get_conv(conv_id, current_user, db)
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return {
        "conversation": _conv_dict(conv),
        "messages":     [_msg_dict(m) for m in messages],
        "message_count": len(messages),
    }


# ── DELETE /conversations/{conv_id} ──────────────────────────────────────────

@router.delete("/{conv_id}", summary="Archive a conversation")
def archive_conversation(
    conv_id: str,
    request: Request,
    db:      Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = _get_conv(conv_id, current_user, db)
    conv.is_archived = True

    log_action(
        db=db, action="CONVERSATION_DELETED",
        actor=current_user, org_id=str(conv.organization_id),
        resource_type="conversation", resource_id=conv_id,
        resource_name=conv.title, request=request,
    )
    db.commit()
    return {"message": "Conversation archived"}


# ── GET /conversations/{conv_id}/messages ─────────────────────────────────────

@router.get("/{conv_id}/messages", summary="List messages in a conversation")
def list_messages(
    conv_id: str,
    db:      Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = _get_conv(conv_id, current_user, db)
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
        .all()
    )
    return {"conversation_id": conv_id, "messages": [_msg_dict(m) for m in messages]}


# ── POST /conversations/{conv_id}/messages ────────────────────────────────────

@router.post("/{conv_id}/messages", status_code=201, summary="Add message to conversation")
def add_message(
    conv_id: str,
    payload: AddMessageRequest,
    db:      Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    conv = _get_conv(conv_id, current_user, db)

    msg = Message(
        organization_id = conv.organization_id,
        conversation_id = conv.id,
        role            = payload.role,
        content         = payload.content,
        token_count     = payload.token_count,
        provider        = payload.provider,
        model           = payload.model,
        cost            = payload.cost,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return {"message": _msg_dict(msg)}


# ── DELETE /conversations/{conv_id}/messages/{msg_id} ────────────────────────

@router.delete(
    "/{conv_id}/messages/{msg_id}",
    summary="Delete a single message (admin only)",
)
def delete_message(
    conv_id: str,
    msg_id:  str,
    request: Request,
    db:      Session = Depends(get_db),
    _admin:  User    = Depends(require_admin),
):
    msg = db.query(Message).filter(
        Message.id == uuid.UUID(msg_id),
        Message.conversation_id == uuid.UUID(conv_id),
    ).first()
    if not msg:
        raise HTTPException(404, "Message not found")

    log_action(
        db=db, action="CONVERSATION_DELETED",
        actor=_admin, org_id=str(msg.organization_id),
        resource_type="message", resource_id=msg_id,
        request=request,
    )
    db.delete(msg)
    db.commit()
    return {"message": "Message deleted"}


# ── Admin: list org conversations ─────────────────────────────────────────────

@router.get(
    "/org/{org_id}/all",
    summary="List ALL conversations in org (admin only — requires view_conversations permission)",
)
def admin_list_conversations(
    org_id:  str,
    user_id: Optional[str] = None,
    limit:   int = 50,
    offset:  int = 0,
    db:      Session = Depends(get_db),
    _perm:   dict    = Depends(require_permission("view_conversations")),
):
    q = db.query(Conversation).filter(
        Conversation.organization_id == uuid.UUID(org_id),
        Conversation.is_archived     == False,
    )
    if user_id:
        q = q.filter(Conversation.user_id == uuid.UUID(user_id))

    total = q.count()
    convs = q.order_by(Conversation.updated_at.desc()).limit(limit).offset(offset).all()

    return {
        "total":         total,
        "conversations": [_conv_dict(c) for c in convs],
    }


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_conv(conv_id: str, current_user: User, db: Session) -> Conversation:
    """Get conversation — user can only see their own unless admin."""
    try:
        uid = uuid.UUID(conv_id)
    except ValueError:
        raise HTTPException(400, "Invalid conversation_id")

    conv = db.query(Conversation).filter(Conversation.id == uid).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")

    # Users can only see their own conversations
    if current_user.role not in ("admin", "super_admin") and conv.user_id != current_user.id:
        raise HTTPException(403, "Access denied")

    return conv
