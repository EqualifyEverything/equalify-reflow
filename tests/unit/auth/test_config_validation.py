"""Settings ``model_validator`` enforces per-mode auth requirements at startup."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError

from src.config import Settings


@pytest.mark.unit
def test_none_mode_needs_no_auth_secret() -> None:
    s = Settings(auth_mode="none")  # type: ignore[arg-type]
    assert s.auth_mode == "none"


@pytest.mark.unit
def test_basic_mode_requires_secret_and_users() -> None:
    with pytest.raises(ValidationError, match="AUTH_SECRET_KEY"):
        Settings(auth_mode="basic")  # type: ignore[arg-type]

    # Secret too short.
    with pytest.raises(ValidationError, match="at least 32"):
        Settings(auth_mode="basic", auth_secret_key="short", auth_basic_users="alice:$argon2id$xxx")  # type: ignore[arg-type]


@pytest.mark.unit
def test_basic_mode_rejects_empty_user_csv() -> None:
    with pytest.raises(ValidationError, match="must contain at least one"):
        Settings(  # type: ignore[arg-type]
            auth_mode="basic",
            auth_secret_key="x" * 32,
            auth_basic_users="",
        )


@pytest.mark.unit
def test_basic_mode_accepts_real_argon_hash() -> None:
    h = PasswordHasher().hash("hunter2")
    s = Settings(  # type: ignore[arg-type]
        auth_mode="basic",
        auth_secret_key="x" * 32,
        auth_basic_users=f"alice:{h}",
    )
    assert s.auth_mode == "basic"


@pytest.mark.unit
def test_oidc_mode_requires_provider_array() -> None:
    with pytest.raises(ValidationError, match="AUTH_OIDC_PROVIDERS"):
        Settings(auth_mode="oidc", auth_secret_key="x" * 32)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="non-empty JSON array"):
        Settings(  # type: ignore[arg-type]
            auth_mode="oidc",
            auth_secret_key="x" * 32,
            auth_oidc_providers="[]",
        )


@pytest.mark.unit
def test_oidc_mode_validates_required_keys() -> None:
    with pytest.raises(ValidationError, match="missing keys"):
        Settings(  # type: ignore[arg-type]
            auth_mode="oidc",
            auth_secret_key="x" * 32,
            auth_oidc_providers='[{"id": "entra"}]',
        )
