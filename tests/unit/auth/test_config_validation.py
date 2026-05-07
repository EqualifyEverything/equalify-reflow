"""Settings ``model_validator`` enforces per-mode auth requirements at startup.

These tests pin ``_env_file=None`` so the local ``.env`` cannot leak in and
mask a missing-required-field assertion. We're testing the validator's logic
in isolation, not whatever the developer happens to have configured locally.
"""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError
from src.config import Settings


def _settings(**overrides: object) -> Settings:
    """Build a Settings strictly from kwargs — no .env, no environ."""
    return Settings(_env_file=None, _env_file_encoding=None, **overrides)  # type: ignore[arg-type]


@pytest.mark.unit
def test_none_mode_needs_no_auth_secret() -> None:
    s = _settings(auth_mode="none")
    assert s.auth_mode == "none"


@pytest.mark.unit
def test_basic_mode_requires_secret_and_users() -> None:
    with pytest.raises(ValidationError, match="AUTH_SECRET_KEY"):
        _settings(auth_mode="basic")

    # Secret too short.
    with pytest.raises(ValidationError, match="at least 32"):
        _settings(
            auth_mode="basic",
            auth_secret_key="short",
            auth_basic_users="alice:$argon2id$xxx",
        )


@pytest.mark.unit
def test_basic_mode_rejects_empty_user_csv() -> None:
    with pytest.raises(ValidationError, match="must contain at least one"):
        _settings(
            auth_mode="basic",
            auth_secret_key="x" * 32,
            auth_basic_users="",
        )


@pytest.mark.unit
def test_basic_mode_accepts_real_argon_hash() -> None:
    h = PasswordHasher().hash("hunter2")
    s = _settings(
        auth_mode="basic",
        auth_secret_key="x" * 32,
        auth_basic_users=f"alice:{h}",
    )
    assert s.auth_mode == "basic"


@pytest.mark.unit
def test_oidc_mode_requires_provider_array() -> None:
    with pytest.raises(ValidationError, match="AUTH_OIDC_PROVIDERS"):
        _settings(auth_mode="oidc", auth_secret_key="x" * 32)
    with pytest.raises(ValidationError, match="non-empty JSON array"):
        _settings(
            auth_mode="oidc",
            auth_secret_key="x" * 32,
            auth_oidc_providers="[]",
        )


@pytest.mark.unit
def test_oidc_mode_validates_required_keys() -> None:
    with pytest.raises(ValidationError, match="missing keys"):
        _settings(
            auth_mode="oidc",
            auth_secret_key="x" * 32,
            auth_oidc_providers='[{"id": "entra"}]',
        )
