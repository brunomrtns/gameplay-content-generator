"""Auth and user management API routes — BI Identity SSO.

Endpoints:
  GET  /api/auth/me             — get current user info (from BI Identity)
  GET  /api/auth/sso-redirect   — redirect to BI Identity login
  POST /api/auth/logout         — logout (clears local state, redirects to /id/login)
  GET  /api/auth/users          — list all local users (admin only)
  DELETE /api/auth/users/{id}   — delete user (admin only)
  PUT  /api/auth/users/{id}     — update user (admin only — toggle active)
"""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.core.models import User, Automation
from gpcg.infrastructure.auth import (
    get_admin_user,
    get_current_user,
    _validate_bi_user,
    _is_gpcg_admin,
)
from gpcg.infrastructure.database import get_db, session_scope

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────────────────────


class UserResponse(BaseModel):
    id: int
    email: str
    name: Optional[str] = None
    is_admin: bool
    is_active: bool
    has_youtube: bool = False
    channel_title: Optional[str] = None
    created_at: str


class AdminUpdateUserRequest(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _user_to_response(user: User, request: Request = None) -> dict:
    """Convert User to response dict with YouTube connection status and BI admin flag."""
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

    # Determine admin status from BI Identity (not local is_admin flag)
    is_admin = user.is_admin
    if request is not None:
        bi_user = _validate_bi_user(request)
        if bi_user and bi_user.get("email", "").lower() == user.email.lower():
            is_admin = _is_gpcg_admin(bi_user)

    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_admin": is_admin,
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


@router.get("/sso-redirect")
def sso_redirect():
    """Redirect to BI Identity login page with return redirect to GPCG dashboard."""
    return RedirectResponse(
        url="/id/login?redirect=/gpcg/dashboard",
        status_code=302,
    )


@router.get("/me")
def get_me(user: User = Depends(get_current_user), request: Request = None):
    """Get current user info. Returns local user data augmented with BI admin status."""
    return _user_to_response(user, request)


@router.post("/logout")
def logout(request: Request):
    """Logout — proxies to BI Identity Service to revoke tokens and clear cookies.

    The frontend SHOULD call /id/api/auth/logout directly (via ssoLogout) for
    proper cookie clearing. This endpoint exists as a backend fallback and
    also revokes the refresh token server-side.
    """
    settings = get_settings()
    bi_auth = request.cookies.get("bi_auth")
    bi_refresh = request.cookies.get("bi_refresh")

    # Forward the logout request to BI Identity so it revokes the refresh
    # token and clears the SSO cookies. We pass both cookies so the Identity
    # Service can identify the user.
    if bi_auth or bi_refresh:
        try:
            httpx.post(
                f"{settings.bi_identity_url}/api/auth/logout",
                cookies={
                    **({"bi_auth": bi_auth} if bi_auth else {}),
                    **({"bi_refresh": bi_refresh} if bi_refresh else {}),
                },
                json={"refreshToken": bi_refresh} if bi_refresh else {},
                timeout=5.0,
            )
        except Exception:
            pass  # Best-effort — frontend also calls /id/api/auth/logout

    return {"redirect": "/id/login"}


@router.get("/users")
def list_users(
    request: Request,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """List all local users. Admin only."""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [_user_to_response(u, request) for u in users]


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    admin: User = Depends(get_admin_user),
    db: Session = Depends(get_db),
):
    """Delete a user. Admin only. Cannot delete self."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
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
    """Update a user (toggle active, update name). Admin only.

    Note: is_admin is managed by BI Identity, not locally. The local is_admin
    flag is kept for backward compatibility but admin status is determined
    by BI Identity roles at request time.
    """
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    with session_scope() as session:
        u = session.get(User, user_id)
        if req.name is not None:
            u.name = req.name
        if req.is_active is not None:
            u.is_active = req.is_active
        session.flush()
        return _user_to_response(u)
