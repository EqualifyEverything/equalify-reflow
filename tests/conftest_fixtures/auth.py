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
from fastapi import FastAPI
from fastapi.testclient import TestClient


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
