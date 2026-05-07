"""``SessionAuthMiddleware`` re-issues a session cookie past half-life.

Three cases pinned end-to-end against the real middleware:

1. **Fresh session** → no ``Set-Cookie`` on a normal response.
2. **Backdated session past half-life** → middleware mints a fresh cookie.
3. **Response already carries the ``X-Auth-Cookie-Set`` sentinel** (the
   pattern used by login and logout routes) → middleware does NOT
   re-issue, even when the session is past half-life. Prevents the
   double-write race that would resurrect a logged-out session.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from pydantic import SecretStr
from src.auth.base import Identity
from src.auth.middleware import SessionAuthMiddleware

SESSION_COOKIE = "reflow_session"


def _backdated_identity(*, age_seconds: int, ttl_seconds: int) -> Identity:
    """Build an Identity whose ``issued_at``/``expires_at`` are shifted
    back by ``age_seconds`` while preserving a total span of ``ttl_seconds``.

    A pair where elapsed > ttl/2 lands past half-life and triggers re-issue.
    """
    now = datetime.now(UTC)
    issued_at = now - timedelta(seconds=age_seconds)
    expires_at = issued_at + timedelta(seconds=ttl_seconds)
    return Identity(
        sub="alice",
        email=None,
        name="alice",
        provider_id="basic",
        issued_at=issued_at,
        expires_at=expires_at,
    )


@pytest.fixture
def reissue_app() -> Iterator[tuple[FastAPI, str]]:
    """Build a minimal app with ``SessionAuthMiddleware`` and two test routes:
    ``/ping`` (no special headers), ``/cookie-already-set`` (sets the
    sentinel header so the middleware suppresses re-issue).

    Yields ``(app, secret_key)`` so callers can encode their own cookies.
    """
    from src.auth import factory
    from src.config import settings as global_settings

    secret = "x" * 32
    overrides = {
        "auth_mode": "basic",
        "auth_secret_key": SecretStr(secret),
        "auth_session_cookie_name": SESSION_COOKIE,
        "auth_session_ttl_seconds": 3600,
        "auth_cookie_secure": False,
    }
    saved: dict[str, Any] = {k: getattr(global_settings, k) for k in overrides}
    for k, v in overrides.items():
        object.__setattr__(global_settings, k, v)
    factory.get_auth_provider.cache_clear()
    factory.get_session_store.cache_clear()

    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware)

    @app.get("/ping")
    async def _ping() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/cookie-already-set")
    async def _already_set() -> Response:
        response = Response(content='{"ok":"true"}', media_type="application/json")
        # Mimic what login/logout routes do — opt out of middleware
        # re-issue for this response so we don't double-write.
        response.headers["X-Auth-Cookie-Set"] = "1"
        return response

    try:
        yield app, secret
    finally:
        for k, v in saved.items():
            object.__setattr__(global_settings, k, v)
        factory.get_auth_provider.cache_clear()
        factory.get_session_store.cache_clear()


def _encode(identity: Identity, secret: str) -> str:
    """Mint a cookie value the same way SignedCookieSession.encode would."""
    from src.auth.session import SignedCookieSession

    return SignedCookieSession(secret_key=secret, max_age_seconds=3600).encode(identity)


@pytest.mark.integration
def test_fresh_session_does_not_reissue(  # type: ignore[no-untyped-def]
    reissue_app,
) -> None:
    app, secret = reissue_app
    fresh = _backdated_identity(age_seconds=10, ttl_seconds=3600)
    cookie = _encode(fresh, secret)

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, cookie)
    resp = client.get("/ping")

    assert resp.status_code == 200
    assert SESSION_COOKIE not in {
        _name_from_set_cookie(h) for h in resp.headers.get_list("set-cookie")
    }


@pytest.mark.integration
def test_session_past_half_life_is_reissued(  # type: ignore[no-untyped-def]
    reissue_app,
) -> None:
    app, secret = reissue_app
    # ttl=3600 → halflife=1800 → age=2000 lands past halflife.
    aged = _backdated_identity(age_seconds=2000, ttl_seconds=3600)
    cookie = _encode(aged, secret)

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, cookie)
    resp = client.get("/ping")

    assert resp.status_code == 200
    set_cookie_names = {
        _name_from_set_cookie(h) for h in resp.headers.get_list("set-cookie")
    }
    assert SESSION_COOKIE in set_cookie_names
    assert f"{SESSION_COOKIE}_csrf" in set_cookie_names


@pytest.mark.integration
def test_sentinel_suppresses_reissue(  # type: ignore[no-untyped-def]
    reissue_app,
) -> None:
    """If the route already wrote the cookie (login/logout path), the
    middleware must NOT clobber it with a sliding re-issue. Otherwise
    logout would race with re-issue and resurrect the session.
    """
    app, secret = reissue_app
    aged = _backdated_identity(age_seconds=2000, ttl_seconds=3600)
    cookie = _encode(aged, secret)

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, cookie)
    resp = client.get("/cookie-already-set")

    assert resp.status_code == 200
    set_cookie_names = {
        _name_from_set_cookie(h) for h in resp.headers.get_list("set-cookie")
    }
    # Route emitted no Set-Cookie itself; middleware suppressed re-issue.
    assert SESSION_COOKIE not in set_cookie_names
    assert f"{SESSION_COOKIE}_csrf" not in set_cookie_names


@pytest.mark.integration
def test_invalid_cookie_does_not_reissue(  # type: ignore[no-untyped-def]
    reissue_app,
) -> None:
    """Tampered/expired cookie must be treated as anonymous — and an
    anonymous request must NOT cause the middleware to mint a session.
    """
    app, _secret = reissue_app
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "obviously-not-a-valid-signed-cookie")
    resp = client.get("/ping")

    assert resp.status_code == 200
    set_cookie_names = {
        _name_from_set_cookie(h) for h in resp.headers.get_list("set-cookie")
    }
    assert SESSION_COOKIE not in set_cookie_names


def _name_from_set_cookie(header_value: str) -> str:
    return header_value.split("=", 1)[0].strip()
