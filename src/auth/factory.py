"""Provider + session-store wiring.

Read once at startup so every request takes the cached, validated objects.
Settings are immutable for the process lifetime, so memoising here is safe.
"""

from __future__ import annotations

from functools import lru_cache

from ..config import settings
from .base import AuthMode, AuthProvider
from .providers.basic_provider import BasicAuthProvider
from .providers.none_provider import NoneAuthProvider
from .session import SessionStore, SignedCookieSession


@lru_cache(maxsize=1)
def get_auth_provider() -> AuthProvider:
    """Return the active provider for the configured ``auth_mode``.

    Settings validation has already enforced that mode-required env is set,
    so we only handle the modes that actually run.
    """
    mode = AuthMode(settings.auth_mode)
    if mode is AuthMode.NONE:
        return NoneAuthProvider()
    if mode is AuthMode.BASIC:
        # validated by Settings: auth_basic_users is non-None and parses
        users_csv = settings.auth_basic_users.get_secret_value()  # type: ignore[union-attr]
        return BasicAuthProvider(users_csv=users_csv, session_ttl_seconds=settings.auth_session_ttl_seconds)
    if mode is AuthMode.OIDC:
        # Lands in PR2; raise loudly until then so a misconfigured deployment
        # fails at startup rather than silently auth-bypassing.
        raise NotImplementedError("OIDC provider lands in PR2; use AUTH_MODE=basic for now")
    # Defensive — Settings validates the literal so this branch shouldn't run.
    raise ValueError(f"Unknown auth_mode: {settings.auth_mode!r}")


@lru_cache(maxsize=1)
def get_session_store() -> SessionStore:
    """Return the configured session encoder. Phase 1+2 use signed cookies."""
    secret = settings.auth_secret_key
    if secret is None:
        # Should be unreachable when auth_mode != none thanks to Settings
        # validation; raise so a bug surfaces immediately.
        raise RuntimeError("auth_secret_key required when auth is enabled")
    return SignedCookieSession(
        secret_key=secret.get_secret_value(),
        max_age_seconds=settings.auth_session_ttl_seconds,
    )
