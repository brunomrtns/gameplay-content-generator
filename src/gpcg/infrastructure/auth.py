"""BI Identity SSO authentication — cookie-based, validates via Identity Service.

Replaces the old JWT+bcrypt local auth. The Identity Service at /id/ sets
domain-level cookies `bi_auth` (JWT access token, 15min) and `bi_refresh`
(JWT refresh token, 7d). This middleware reads the `bi_auth` cookie and
calls the Identity Service's `/api/auth/check` endpoint to validate the
user. If valid, a local User is found-or-created by email.

Mobile app auth: the mobile app exchanges SSO cookies for a JWT token
via POST /api/auth/token, then sends the token as Bearer in the
Authorization header. This module supports both auth methods:
  1. Cookie-based (web) — bi_auth cookie → BI Identity check
  2. Token-based (mobile) — Bearer JWT → local verification
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.infrastructure.database import get_db
from gpcg.core.models import User

log = logging.getLogger(__name__)

# Cache the BI user object on the request so we don't call /api/auth/check
# multiple times per request (get_current_user + get_admin_user).
_BI_USER_KEY = "_bi_user"
# Cache the local user resolved from Bearer token on the request.
_TOKEN_USER_KEY = "_token_user"
# Key for storing Set-Cookie headers to repass to the browser
_SET_COOKIE_KEY = "_bi_set_cookies"


def _validate_bi_user(request: Request) -> Optional[dict]:
    """Call the BI Identity Service /api/auth/check with forwarded cookies.

    Returns the BI user object dict if valid, None otherwise.
    Caches the result on the request state to avoid duplicate calls.
    Also caches in Redis (TTL 10s) to reduce calls to BI Identity.

    Sends BOTH bi_auth (access token, 15min) and bi_refresh (refresh token,
    7d) cookies so the Identity Service can transparently refresh expired
    access tokens server-side. Without bi_refresh, the backend gets 401
    every 15 minutes and forces a frontend roundtrip for refresh.
    """
    # Return cached result if available (request-scoped)
    cached = getattr(request.state, _BI_USER_KEY, None)
    if cached is not None:
        return cached

    bi_auth = request.cookies.get("bi_auth")
    if not bi_auth:
        return None

    # Try Redis cache (TTL 10s — short window to limit revocation risk)
    import hashlib
    from gpcg.infrastructure.cache import cache_get, cache_set
    cache_key_hash = hashlib.sha256(bi_auth.encode()).hexdigest()[:16]
    cache_key = f"auth:{cache_key_hash}"
    redis_cached = cache_get(cache_key)
    if redis_cached is not None:
        setattr(request.state, _BI_USER_KEY, redis_cached)
        return redis_cached

    # Also forward bi_refresh so BI Identity can refresh expired access tokens
    bi_refresh = request.cookies.get("bi_refresh")
    cookies = {"bi_auth": bi_auth}
    if bi_refresh:
        cookies["bi_refresh"] = bi_refresh

    settings = get_settings()
    try:
        resp = httpx.get(
            f"{settings.bi_identity_url}/api/auth/check",
            cookies=cookies,
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        log.warning(f"BI Identity check failed: {exc}")
        return None

    if resp.status_code != 200:
        log.debug(f"BI Identity check returned {resp.status_code}")
        return None

    # Repass Set-Cookie headers from BI Identity to the browser.
    # When bi_auth expires (15min), BI Identity rotates it using bi_refresh
    # and sends a Set-Cookie with the new token. Without this, the browser
    # never gets the refreshed cookie and the user is logged out after 15min.
    set_cookie_headers = resp.headers.get_list("set-cookie")
    if set_cookie_headers:
        repass = []
        for cookie_header in set_cookie_headers:
            # Only repass bi_auth and bi_refresh cookies (not others)
            if "bi_auth=" in cookie_header or "bi_refresh=" in cookie_header:
                repass.append(cookie_header)
        if repass:
            setattr(request.state, _SET_COOKIE_KEY, repass)

    data = resp.json()
    # /api/auth/check returns the user object directly (not wrapped in {user: ...})
    bi_user = data.get("user") if "user" in data else data
    if not bi_user or not bi_user.get("email"):
        log.warning("BI Identity check returned 200 but no user email")
        return None

    # Cache on request state
    setattr(request.state, _BI_USER_KEY, bi_user)
    # Cache in Redis (TTL 10s — short window for security)
    # NOTE: only cache if the access token was valid (not refreshed) —
    # if BI Identity rotated the cookie, the cached user would be tied to
    # the old token. We detect rotation by checking if Set-Cookie was sent.
    if not set_cookie_headers:
        cache_set(cache_key, bi_user, ttl=10)
    return bi_user


def _find_or_create_local_user(bi_user: dict, db: Session) -> User:
    """Find or create a local User matching the BI user by email.

    Updates name and bi_identity_id if the BI user info has changed.
    Commits the session to persist any changes (get_db() does not commit).
    Handles race conditions where concurrent requests try to create the
    same user — catches IntegrityError and retries the find.
    """
    from sqlalchemy.exc import IntegrityError as _IntegrityError

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
            db.commit()
        return user

    # Create new local user.
    # NOTE: password_hash is NOT NULL in the SQLite schema (legacy from the
    # old local-auth era). SSO users have no local password — BI Identity
    # manages credentials — so we store a sentinel value. The column is never
    # read for SSO users. (SQLite cannot ALTER an existing column's NOT NULL
    # constraint, so we can't make it nullable via _ensure_column.)
    user = User(
        email=email,
        name=name,
        password_hash="!sso-no-local-password",
        bi_identity_id=bi_id,
        is_admin=False,
        is_active=True,
    )
    db.add(user)
    try:
        db.commit()
    except _IntegrityError:
        # Race condition: another concurrent request created the user first.
        # Rollback and re-query to get the existing user.
        db.rollback()
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            raise  # Should not happen, but don't swallow the error
    log.info(f"Created local user '{email}' from BI Identity (bi_id={bi_id})")

    # Ensure the user has a ChannelProfile (default: games domain)
    from gpcg.core.models import ChannelProfile, ContentDomain
    existing_profile = db.query(ChannelProfile).filter(
        ChannelProfile.user_id == user.id
    ).first()
    if not existing_profile:
        profile = ChannelProfile(
            user_id=user.id,
            domain=ContentDomain.games.value,
        )
        db.add(profile)
        db.commit()

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


# ── Mobile Token (JWT) ───────────────────────────────────────────────────────


def issue_mobile_token(user: User) -> str:
    """Issue a JWT token for the mobile app.

    The token encodes the user_id and email, signed with
    gpcg_mobile_token_secret, expiring after gpcg_mobile_token_expiry.
    The mobile app sends this as Bearer token in Authorization header.
    """
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iat": now,
        "exp": now + settings.gpcg_mobile_token_expiry,
        "iss": "gpcg-mobile",
    }
    return jwt.encode(payload, settings.gpcg_mobile_token_secret, algorithm="HS256")


def _validate_bearer_token(request: Request, db: Session) -> Optional[User]:
    """Validate a Bearer JWT token from the Authorization header.

    Returns the local User if valid, None otherwise.
    Caches the result on the request state to avoid duplicate verification.
    """
    # Return cached result if available
    cached = getattr(request.state, _TOKEN_USER_KEY, None)
    if cached is not None:
        return cached

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header[7:]  # strip "Bearer "
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.gpcg_mobile_token_secret,
            algorithms=["HS256"],
            issuer="gpcg-mobile",
        )
    except jwt.PyJWTError:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    user = db.get(User, int(user_id))
    if user is None:
        return None

    # Cache on request state
    setattr(request.state, _TOKEN_USER_KEY, user)
    return user


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: validate auth (cookie OR Bearer token), return local User.

    Supports two auth methods:
    1. Cookie-based (web): reads `bi_auth` cookie, validates via BI Identity.
    2. Token-based (mobile): reads `Authorization: Bearer <token>` header,
       verifies JWT signed with gpcg_mobile_token_secret.

    Raises 401 if neither method yields a valid user.
    """
    # Try Bearer token first (mobile) — fast, no network call
    token_user = _validate_bearer_token(request, db)
    if token_user is not None:
        if not token_user.is_active:
            raise HTTPException(status_code=403, detail="Account deactivated")
        return token_user

    # Fall back to cookie-based auth (web)
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


class CookieRefreshMiddleware(BaseHTTPMiddleware):
    """Repass Set-Cookie headers from BI Identity to the browser.

    When _validate_bi_user detects that BI Identity rotated the bi_auth
    cookie (via bi_refresh), it stores the Set-Cookie headers on
    request.state. This middleware copies them to the actual HTTP response
    so the browser receives the refreshed cookie.

    Without this, the GPCG backend calls BI Identity server-side, gets a
    new cookie in the httpx response, but never sends it to the browser —
    causing the user to be logged out every 15 minutes when the access
    token expires.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # Check if _validate_bi_user stored cookies to repass
        # (only set when BI Identity rotated the token)
        repass = getattr(request.state, _SET_COOKIE_KEY, None)
        if repass:
            for cookie_header in repass:
                response.headers.append("set-cookie", cookie_header)
        return response
