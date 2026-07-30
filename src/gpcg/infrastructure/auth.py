"""BI Identity SSO authentication — cookie-based, validates via Identity Service.

Replaces the old JWT+bcrypt local auth. The Identity Service at /id/ sets
domain-level cookies `bi_auth` (JWT access token, 15min) and `bi_refresh`
(JWT refresh token, 7d). This middleware reads the `bi_auth` cookie and
calls the Identity Service's `/api/auth/check` endpoint to validate the
user. If valid, a local User is found-or-created by email.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.infrastructure.database import get_db
from gpcg.domain.models import User

log = logging.getLogger(__name__)

# Cache the BI user object on the request so we don't call /api/auth/check
# multiple times per request (get_current_user + get_admin_user).
_BI_USER_KEY = "_bi_user"


def _validate_bi_user(request: Request) -> Optional[dict]:
    """Call the BI Identity Service /api/auth/check with forwarded cookies.

    Returns the BI user object dict if valid, None otherwise.
    Caches the result on the request state to avoid duplicate calls.
    """
    # Return cached result if available
    cached = getattr(request.state, _BI_USER_KEY, None)
    if cached is not None:
        return cached

    bi_auth = request.cookies.get("bi_auth")
    if not bi_auth:
        return None

    settings = get_settings()
    try:
        resp = httpx.get(
            f"{settings.bi_identity_url}/api/auth/check",
            cookies={"bi_auth": bi_auth},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        log.warning(f"BI Identity check failed: {exc}")
        return None

    if resp.status_code != 200:
        return None

    bi_user = resp.json().get("user")
    if not bi_user or not bi_user.get("email"):
        return None

    # Cache on request state
    setattr(request.state, _BI_USER_KEY, bi_user)
    return bi_user


def _find_or_create_local_user(bi_user: dict, db: Session) -> User:
    """Find or create a local User matching the BI user by email.

    Updates name and bi_identity_id if the BI user info has changed.
    """
    email = bi_user["email"].lower().strip()
    bi_id = str(bi_user.get("id", ""))
    name = bi_user.get("name") or email.split("@")[0]

    user = db.query(User).filter(User.email == email).first()
    if user:
        # Update BI identity link and name if changed
        changed = False
        if user.bi_identity_id != bi_id:
            user.bi_identity_id = bi_id
            changed = True
        if user.name != name:
            user.name = name
            changed = True
        if changed:
            db.flush()
        return user

    # Create new local user
    user = User(
        email=email,
        name=name,
        password_hash=None,
        bi_identity_id=bi_id,
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    db.flush()
    log.info(f"Created local user '{email}' from BI Identity (bi_id={bi_id})")
    return user


def _is_gpcg_admin(bi_user: dict) -> bool:
    """Check if the BI user has ADMIN role for 'gpcg' system OR isSuperAdmin."""
    if bi_user.get("isSuperAdmin"):
        return True
    roles = bi_user.get("roles") or []
    for role in roles:
        # role can be a dict like {"system": "gpcg", "role": "ADMIN"}
        # or a string like "gpcg:ADMIN"
        if isinstance(role, dict):
            system = role.get("system", "")
            role_name = role.get("role", "")
            if system == "gpcg" and role_name.upper() in ("ADMIN", "SUPER_ADMIN"):
                return True
        elif isinstance(role, str):
            if role.upper() in ("GPCG:ADMIN", "GPCG_ADMIN", "ADMIN"):
                return True
    return False


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: validate BI Identity cookie, return local User.

    Reads the `bi_auth` cookie, calls the Identity Service to validate,
    and finds-or-creates a local User matching by email.

    Raises 401 if no cookie, invalid cookie, or user not found.
    """
    bi_user = _validate_bi_user(request)
    if bi_user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user = _find_or_create_local_user(bi_user, db)
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    return user


def get_admin_user(
    request: Request,
    user: User = Depends(get_current_user),
) -> User:
    """FastAPI dependency: require admin user.

    Checks if the BI user has ADMIN role for 'gpcg' system OR isSuperAdmin.
    """
    bi_user = _validate_bi_user(request)
    if bi_user and _is_gpcg_admin(bi_user):
        return user

    raise HTTPException(status_code=403, detail="Admin access required")


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """FastAPI dependency: return user if authenticated, None otherwise.

    Used for endpoints that work both authenticated and anonymous.
    """
    bi_user = _validate_bi_user(request)
    if bi_user is None:
        return None

    try:
        user = _find_or_create_local_user(bi_user, db)
        if not user.is_active:
            return None
        return user
    except Exception:
        return None
