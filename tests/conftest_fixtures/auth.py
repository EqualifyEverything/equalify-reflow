"""Fixtures for auth integration tests.

The global ``src.main.app`` is built against the Settings singleton at import
time, so we can't switch ``AUTH_MODE`` mid-test. These fixtures construct a
minimal FastAPI app per test against patched settings, exercising the same
middleware and router as production.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# Hoisted to module scope: ``from __future__ import annotations`` defers
# annotation evaluation, and FastAPI looks up route-parameter type hints
# in the route's ``__globals__`` at registration time. If ``Request`` were
# imported inside the fixture function, FastAPI couldn't resolve it and
# would treat ``request: Request`` as a query parameter (422 missing-field).
from src.auth.base import Identity


@pytest.fixture
def basic_password() -> str:
    return "correct-horse-battery-staple"


@pytest.fixture
def basic_users_csv(basic_password: str) -> str:
    """Single-user AUTH_BASIC_USERS CSV, hashed at fixture time."""
    return f"alice:{PasswordHasher().hash(basic_password)}"


def _build_app(settings_overrides: dict[str, Any]) -> FastAPI:
    """Build a fresh FastAPI app with the requested settings.

    We monkey-patch the ``src.config.settings`` singleton's attributes so the
    middleware and routes pick up the new values, then clear the factory's
    ``lru_cache`` so the auth provider is re-resolved.
    """
    from src.auth import factory
    from src.config import settings as global_settings

    # Stash + apply overrides
    saved: dict[str, Any] = {}
    for key, value in settings_overrides.items():
        saved[key] = getattr(global_settings, key)
        object.__setattr__(global_settings, key, value)

    factory.get_auth_provider.cache_clear()
    factory.get_session_store.cache_clear()

    app = FastAPI()
    if global_settings.auth_mode != "none":
        from src.auth.middleware import SessionAuthMiddleware

        app.add_middleware(SessionAuthMiddleware)

    from src.auth.routes import router as auth_router

    app.include_router(auth_router)

    # Restore on teardown by stashing the saved dict on the app for the
    # caller fixture to clean up.
    app.state._auth_test_saved = saved
    return app


def _restore(app: FastAPI) -> None:
    from src.auth import factory
    from src.config import settings as global_settings

    saved = getattr(app.state, "_auth_test_saved", {})
    for key, value in saved.items():
        object.__setattr__(global_settings, key, value)
    factory.get_auth_provider.cache_clear()
    factory.get_session_store.cache_clear()


@pytest.fixture
def auth_basic_app(basic_users_csv: str) -> Iterator[FastAPI]:
    """Minimal app wired for AUTH_MODE=basic."""
    from pydantic import SecretStr

    app = _build_app(
        {
            "auth_mode": "basic",
            "auth_secret_key": SecretStr("x" * 32),
            "auth_basic_users": SecretStr(basic_users_csv),
            "auth_cookie_secure": False,  # TestClient runs over HTTP
            "auth_session_cookie_name": "reflow_session",
            "auth_session_ttl_seconds": 3600,
        }
    )
    try:
        yield app
    finally:
        _restore(app)


@pytest.fixture
def auth_basic_client(auth_basic_app: FastAPI) -> TestClient:
    return TestClient(auth_basic_app)


@pytest.fixture
def auth_none_app() -> Iterator[FastAPI]:
    app = _build_app({"auth_mode": "none"})
    try:
        yield app
    finally:
        _restore(app)


@pytest.fixture
def auth_none_client(auth_none_app: FastAPI) -> TestClient:
    return TestClient(auth_none_app)


def _restore_settings(saved: dict[str, Any]) -> None:
    """Roll back settings overrides — used when a fixture builds its own app."""
    from src.auth import factory
    from src.config import settings as global_settings

    for key, value in saved.items():
        object.__setattr__(global_settings, key, value)
    factory.get_auth_provider.cache_clear()
    factory.get_session_store.cache_clear()


@pytest.fixture
def auth_full_app(basic_users_csv: str) -> Iterator[FastAPI]:
    """App that mirrors production's middleware composition.

    Both ``SessionAuthMiddleware`` (when AUTH_MODE != none) and
    ``APIKeyAuthMiddleware`` (when ENABLE_API_KEY_AUTH=True) are wired in
    the same order as ``src/main.py``: API-key middleware added first
    (innermost), session middleware added second (outermost). A protected
    route at ``/api/v1/test/ping`` lets us assert end-to-end behaviour.
    """
    from pydantic import SecretStr
    from src.auth import factory
    from src.auth.middleware import SessionAuthMiddleware
    from src.auth.routes import router as auth_router
    from src.config import settings as global_settings
    from src.middleware.api_key_auth import APIKeyAuthMiddleware

    overrides: dict[str, Any] = {
        "auth_mode": "basic",
        "auth_secret_key": SecretStr("x" * 32),
        "auth_basic_users": SecretStr(basic_users_csv),
        "auth_cookie_secure": False,
        "auth_session_cookie_name": "reflow_session",
        "auth_session_ttl_seconds": 3600,
        "enable_api_key_auth": True,
        "api_keys": SecretStr("test-api-key-1,test-api-key-2"),
    }
    saved: dict[str, Any] = {}
    for key, value in overrides.items():
        saved[key] = getattr(global_settings, key)
        object.__setattr__(global_settings, key, value)
    factory.get_auth_provider.cache_clear()
    factory.get_session_store.cache_clear()

    app = FastAPI()
    # Order matches src/main.py: APIKeyAuth added first → innermost.
    # SessionAuth added second → outermost → runs first → stamps identity →
    # APIKeyAuth then short-circuits on it.
    app.add_middleware(APIKeyAuthMiddleware)
    app.add_middleware(SessionAuthMiddleware)
    app.include_router(auth_router)

    @app.get("/api/v1/test/ping")
    async def _ping(request: Request) -> dict[str, str | None]:
        identity = getattr(request.state, "identity", None)
        return {
            "user": identity.sub if isinstance(identity, Identity) else None,
        }

    try:
        yield app
    finally:
        _restore_settings(saved)


@pytest.fixture
def auth_full_client(auth_full_app: FastAPI) -> TestClient:
    return TestClient(auth_full_app)
