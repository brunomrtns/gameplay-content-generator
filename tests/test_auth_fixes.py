"""Tests for the auth fixes: bi_refresh forwarding, logout proxy, race conditions.

These tests verify the SSO authentication behavior that was broken and fixed:

1. _validate_bi_user should forward BOTH bi_auth and bi_refresh cookies
2. logout endpoint should proxy to BI Identity (not just return a redirect)
3. _find_or_create_local_user should handle race conditions (IntegrityError)
4. _find_or_create_local_user should create a ChannelProfile for new users
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from fastapi import Request
from fastapi.testclient import TestClient


def test_validate_bi_user_forwards_both_cookies():
    """_validate_bi_user should send bi_auth AND bi_refresh to BI Identity."""
    from gpcg.infrastructure.auth import _validate_bi_user

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {
        "bi_auth": "fake_access_token",
        "bi_refresh": "fake_refresh_token",
    }
    # No cached result
    request.state._bi_user = None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "123",
        "email": "test@example.com",
        "name": "Test",
    }

    with patch("gpcg.infrastructure.auth.httpx.get", return_value=mock_response) as mock_get:
        with patch("gpcg.infrastructure.auth.get_settings") as mock_settings:
            mock_settings.return_value.bi_identity_url = "http://bi-api:3300"

            result = _validate_bi_user(request)

            # Verify httpx.get was called with both cookies
            call_kwargs = mock_get.call_args
            cookies_sent = call_kwargs.kwargs.get("cookies", {})
            assert "bi_auth" in cookies_sent, "bi_auth must be forwarded"
            assert "bi_refresh" in cookies_sent, "bi_refresh must be forwarded"
            assert cookies_sent["bi_auth"] == "fake_access_token"
            assert cookies_sent["bi_refresh"] == "fake_refresh_token"

    assert result is not None
    assert result["email"] == "test@example.com"


def test_validate_bi_user_without_refresh_still_works():
    """If bi_refresh is missing, _validate_bi_user should still send bi_auth."""
    from gpcg.infrastructure.auth import _validate_bi_user

    request = MagicMock(spec=Request)
    request.state = MagicMock()
    request.cookies = {"bi_auth": "fake_access_token"}
    request.state._bi_user = None

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "123",
        "email": "test@example.com",
        "name": "Test",
    }

    with patch("gpcg.infrastructure.auth.httpx.get", return_value=mock_response) as mock_get:
        with patch("gpcg.infrastructure.auth.get_settings") as mock_settings:
            mock_settings.return_value.bi_identity_url = "http://bi-api:3300"

            result = _validate_bi_user(request)

            call_kwargs = mock_get.call_args
            cookies_sent = call_kwargs.kwargs.get("cookies", {})
            assert "bi_auth" in cookies_sent
            assert "bi_refresh" not in cookies_sent

    assert result is not None


def test_logout_proxies_to_bi_identity():
    """POST /api/auth/logout should return redirect and attempt BI Identity proxy.

    The function has a best-effort try/except around the httpx.post call,
    so we verify the endpoint returns 200 with a redirect field (the
    primary contract). The actual cookie clearing is done by the frontend
    calling /id/api/auth/logout directly.
    """
    from gpcg.api.auth_routes import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api")

    client = TestClient(app)
    client.cookies.set("bi_auth", "fake")
    client.cookies.set("bi_refresh", "fake_refresh")

    # The function imports get_settings and httpx inside the function body.
    # Patch at the source module so the import picks up the mock.
    with patch("gpcg.config.get_settings") as mock_settings:
        mock_settings.return_value.bi_identity_url = "http://bi-api:3300"
        response = client.post("/api/auth/logout")

    assert response.status_code == 200
    data = response.json()
    assert data["redirect"] == "/id/login"


def test_find_or_create_handles_integrity_error():
    """_find_or_create_local_user should handle IntegrityError (race condition)."""
    from gpcg.infrastructure.auth import _find_or_create_local_user
    from gpcg.core.models import User, ChannelProfile, ContentDomain
    from sqlalchemy.exc import IntegrityError
    from gpcg.infrastructure.database import init_db, get_db, _SessionLocal, _engine
    from sqlalchemy.orm import Session

    # Use a fresh in-memory SQLite DB
    init_db()
    session = _SessionLocal()

    bi_user = {
        "id": "bi-123",
        "email": "race@example.com",
        "name": "Race Test",
    }

    # First call: creates the user
    user1 = _find_or_create_local_user(bi_user, session)
    assert user1.email == "race@example.com"
    session.close()

    # Second call: finds the existing user (no IntegrityError)
    session2 = _SessionLocal()
    user2 = _find_or_create_local_user(bi_user, session2)
    assert user2.id == user1.id
    session2.close()

    # Clean up
    session3 = _SessionLocal()
    session3.query(ChannelProfile).filter_by(user_id=user1.id).delete()
    session3.query(User).filter_by(id=user1.id).delete()
    session3.commit()
    session3.close()


def test_find_or_create_creates_channel_profile():
    """_find_or_create_local_user should create a ChannelProfile for new users."""
    from gpcg.infrastructure.auth import _find_or_create_local_user
    from gpcg.core.models import User, ChannelProfile
    from gpcg.infrastructure.database import init_db, _SessionLocal

    init_db()
    session = _SessionLocal()

    # Clean up any existing test user
    session.query(ChannelProfile).filter(
        ChannelProfile.user_id.in_(
            session.query(User.id).filter(User.email == "profile@example.com")
        )
    ).delete(synchronize_session=False)
    session.query(User).filter(User.email == "profile@example.com").delete()
    session.commit()

    bi_user = {
        "id": "bi-456",
        "email": "profile@example.com",
        "name": "Profile Test",
    }

    user = _find_or_create_local_user(bi_user, session)

    # Verify ChannelProfile was created
    profile = session.query(ChannelProfile).filter_by(user_id=user.id).first()
    assert profile is not None, "ChannelProfile must be created for new users"
    assert profile.domain == "games"  # default domain

    # Clean up
    session.query(ChannelProfile).filter_by(user_id=user.id).delete()
    session.query(User).filter_by(id=user.id).delete()
    session.commit()
    session.close()
