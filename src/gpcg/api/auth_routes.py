"""Auth and user management API routes.

Endpoints:
  POST /api/auth/login          — login with email/password
  POST /api/auth/register       — register new user (admin only)
  GET  /api/auth/me             — get current user info
  PUT  /api/auth/me             — update current user (name, password)
  POST /api/auth/reset-password — admin resets a user's password
  GET  /api/auth/users          — list all users (admin only)
  DELETE /api/auth/users/{id}   — delete user (admin only)
  PUT  /api/auth/users/{id}     — update user (admin only — toggle active/admin)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.domain.models import User, Automation
from gpcg.infrastructure.auth import (
    create_access_token,
    get_admin_user,
    get_current_user,
    hash_password,
    verify_password,
)
from gpcg.infrastructure.database import get_db, session_scope

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    is_admin: bool
    is_active: bool
    has_youtube: bool = False
    channel_title: Optional[str] = None
    created_at: str


class UpdateUserRequest(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None


class AdminUpdateUserRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None


class ResetPasswordRequest(BaseModel):
    password: str


class RegisterRequest(BaseModel):
    email: str
    name: Optional[str] = None
    password: str


# ── Helpers ──────────────────────────────────────────────────────────────────


def _user_to_response(user: User) -> dict:
    """Convert User to response dict with YouTube connection status."""
    from gpcg.infrastructure.google_integration_adapter import GoogleIntegrationAdapter

    has_yt = False
    channel_title = None
    if user.google_user_id:
        try:
            adapter = GoogleIntegrationAdapter()
            status = adapter.get_auth_status(user.google_user_id)
            if status.get("connected"):
                has_yt = True
                channel_title = status.get("channelTitle")
        except Exception:
            pass  # google-integration may be down; don't fail the request

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
        "has_youtube": has_yt,
        "channel_title": channel_title,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _ensure_automation(user_id: int, session: Session = None) -> None:
    """Ensure the user has an Automation row (one per user).

    If a session is provided, use it (avoids nested sessions with SQLite).
    Otherwise, open a new session.
    """
    if session:
        existing = session.query(Automation).filter(Automation.user_id == user_id).first()
        if not existing:
            automation = Automation(
                user_id=user_id,
                name="Minha Automação",
                status="idle",
                config={},
                upload_config={},
            )
            session.add(automation)
            session.flush()
    else:
        with session_scope() as sess:
            existing = sess.query(Automation).filter(Automation.user_id == user_id).first()
            if not existing:
                automation = Automation(
                    user_id=user_id,
                    name="Minha Automação",
                    status="idle",
                    config={},
                    upload_config={},
                )
                sess.add(automation)
                sess.flush()


# ── Routes ───────────────────────────────────────────────────────────────────


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    """Login with email and password. Returns JWT token and user info."""
    user = db.query(User).filter(User.email == req.email.lower().strip()).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Email ou senha inválidos")
    if not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email ou senha inválidos")

    # Check if user must reset password (first login with temp password)
    must_reset = (user.metadata_json or {}).get("must_reset", False)

    token = create_access_token(user.id, is_admin=user.is_admin)
    # Ensure automation exists (outside the db session to avoid locks)
    _ensure_automation(user.id)
    return {
        "token": token,
        "user": _user_to_response(user),
        "must_reset_password": must_reset,
    }


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    """Get current user info."""
    return _user_to_response(user)


@router.put("/me")
def update_me(
    req: UpdateUserRequest,
    user: User = Depends(get_current_user),
):
    """Update current user's name and/or password."""
    with session_scope() as session:
        u = session.get(User, user.id)
        if req.name is not None:
            u.name = req.name
        if req.password is not None:
            if len(req.password) < 6:
                raise HTTPException(status_code=400, detail="Senha deve ter no mínimo 6 caracteres")
            u.password_hash = hash_password(req.password)
            # Clear must_reset flag
            meta = dict(u.metadata_json or {})
            meta.pop("must_reset", None)
            meta.pop("temp_password", None)
            u.metadata_json = meta
        session.flush()
        return _user_to_response(u)


@router.post("/register")
def register_user(
    req: RegisterRequest,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Register a new user. Admin only."""
    email = req.email.lower().strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Email inválido")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter no mínimo 6 caracteres")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=409, detail="Email já cadastrado")

    with session_scope() as session:
        user = User(
            email=email,
            name=req.name or email.split("@")[0],
            password_hash=hash_password(req.password),
            is_admin=False,
            is_active=True,
        )
        session.add(user)
        session.flush()
        _ensure_automation(user.id, session)
        return _user_to_response(user)


@router.get("/users")
def list_users(
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all users. Admin only."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_user_to_response(u) for u in users]


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a user. Admin only. Cannot delete self or the admin email."""
    settings = get_settings()
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if user.email == settings.gpcg_admin_email:
        raise HTTPException(status_code=400, detail="Não é possível excluir o admin principal")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Não é possível excluir a si mesmo")

    with session_scope() as session:
        u = session.get(User, user_id)
        # Delete automation
        session.query(Automation).filter(Automation.user_id == user_id).delete()
        session.delete(u)
        session.flush()
    return {"success": True}


@router.put("/users/{user_id}")
def admin_update_user(
    user_id: int,
    req: AdminUpdateUserRequest,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Update a user (toggle active/admin). Admin only."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    settings = get_settings()
    with session_scope() as session:
        u = session.get(User, user_id)
        if req.name is not None:
            u.name = req.name
        if req.is_active is not None:
            if u.email == settings.gpcg_admin_email and not req.is_active:
                raise HTTPException(status_code=400, detail="Não é possível desativar o admin principal")
            u.is_active = req.is_active
        if req.is_admin is not None:
            u.is_admin = req.is_admin
        session.flush()
        return _user_to_response(u)


@router.post("/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    req: ResetPasswordRequest,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Reset a user's password. Admin only."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter no mínimo 6 caracteres")

    with session_scope() as session:
        u = session.get(User, user_id)
        u.password_hash = hash_password(req.password)
        # Clear must_reset flag
        meta = dict(u.metadata_json or {})
        meta.pop("must_reset", None)
        meta.pop("temp_password", None)
        u.metadata_json = meta
        session.flush()
    return {"success": True}
