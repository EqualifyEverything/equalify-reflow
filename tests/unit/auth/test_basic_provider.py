"""Unit tests for the basic-auth provider."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher
from src.auth.providers.basic_provider import (
    BasicAuthProvider,
    InvalidCredentialsError,
    _parse_users,
)


def _csv_for(users: dict[str, str]) -> str:
    """Build an AUTH_BASIC_USERS-style entry list from {username: plaintext_password}."""
    hasher = PasswordHasher()
    parts = []
    for username, password in users.items():
        parts.append(f"{username}:{hasher.hash(password)}")
    # Semicolon-separated to avoid colliding with argon2's m=…,t=…,p=… block.
    return ";".join(parts)


@pytest.mark.unit
def test_parse_skips_malformed_entries() -> None:
    raw = "alice:$argon2id$v=19$xxx;malformed;bob:not-an-argon-hash;:$argon2id$$"
    parsed = _parse_users(raw)
    # Only the alice entry survives; the rest are rejected for missing colon,
    # missing $argon2 prefix, or missing username.
    assert set(parsed.keys()) == {"alice"}


@pytest.mark.unit
def test_authenticate_returns_identity_for_valid_password() -> None:
    csv = _csv_for({"alice": "correct-horse"})
    provider = BasicAuthProvider(users_csv=csv, session_ttl_seconds=3600)
    identity = provider.authenticate(username="alice", password="correct-horse")
    assert identity.sub == "alice"
    assert identity.provider_id == "basic"
    assert identity.name == "alice"


@pytest.mark.unit
def test_wrong_password_raises_invalid_credentials() -> None:
    csv = _csv_for({"alice": "correct-horse"})
    provider = BasicAuthProvider(users_csv=csv, session_ttl_seconds=3600)
    with pytest.raises(InvalidCredentialsError):
        provider.authenticate(username="alice", password="battery-staple")


@pytest.mark.unit
def test_unknown_user_raises_invalid_credentials() -> None:
    csv = _csv_for({"alice": "correct-horse"})
    provider = BasicAuthProvider(users_csv=csv, session_ttl_seconds=3600)
    with pytest.raises(InvalidCredentialsError):
        provider.authenticate(username="bob", password="anything")


@pytest.mark.unit
def test_empty_user_set_initialises_but_rejects_all() -> None:
    provider = BasicAuthProvider(users_csv="", session_ttl_seconds=3600)
    with pytest.raises(InvalidCredentialsError):
        provider.authenticate(username="alice", password="anything")
