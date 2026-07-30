"""JWT authentication and password hashing for multi-user support."""

from __future__ import annotations

import bcrypt
import jwt
import time
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from gpcg.config import get_settings
from gpcg.infrastructure.database import get_db
from gpcg.domain.models import User

_security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Hash a password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(user_id: int, is_admin: bool = False) -> str:
    """Create a JWT access token."""
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "admin": is_admin,
        "iat": now,
        "exp": now + settings.gpcg_jwt_expiry,
    }
    return jwt.encode(payload, settings.gpcg_jwt_secret, algorithm="HS256")


def decode_token(token: str) -> dict:
    """Decode and verify a JWT token. Raises on invalid."""
    settings = get_settings()
    return jwt.decode(token, settings.gpcg_jwt_secret, algorithms=["HS256"])


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency: extract and validate the JWT, return the User.

    Raises 401 if no token, invalid token, or user not found.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    user_id = int(payload.get("sub", 0))
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User not found")

    return user


def get_admin_user(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency: require admin user."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    """FastAPI dependency: return user if token present, None otherwise.
    Used for endpoints that work both authenticated and anonymous.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    try:
        payload = decode_token(token)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None
    user_id = int(payload.get("sub", 0))
    if not user_id:
        return None
    return db.get(User, user_id)
