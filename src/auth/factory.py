"""Provider + session-store wiring.

Read once at startup so every request takes the cached, validated objects.
Settings are immutable for the process lifetime, so memoising here is safe.
"""

from __future__ import annotations

import json
from functools import lru_cache

from ..config import settings
from .base import AuthMode, AuthProvider
from .providers.basic_provider import BasicAuthProvider
from .providers.none_provider import NoneAuthProvider
from .providers.oidc_provider import OIDCAuthProvider, OIDCProviderConfig
from .session import SessionStore, SignedCookieSession


@lru_cache(maxsize=1)
def get_auth_providers() -> dict[str, AuthProvider]:
    """All configured providers, keyed by ``provider.id``.

    For ``AUTH_MODE=none`` and ``=basic`` there's exactly one entry. For
    ``=oidc`` there's one entry per element of ``AUTH_OIDC_PROVIDERS`` —
    PR2 typically ships with a single Entra entry; PR3's UI exposes the
    chooser when there are several.
    """
    mode = AuthMode(settings.auth_mode)
    if mode is AuthMode.NONE:
        return {"none": NoneAuthProvider()}
    if mode is AuthMode.BASIC:
        # validated by Settings: auth_basic_users is non-None and parses
        users_csv = settings.auth_basic_users.get_secret_value()  # type: ignore[union-attr]
        return {
            "basic": BasicAuthProvider(
                users_csv=users_csv, session_ttl_seconds=settings.auth_session_ttl_seconds
            )
        }
    if mode is AuthMode.OIDC:
        # validated by Settings: auth_oidc_providers parses to a non-empty
        # JSON array whose entries have the required keys.
        raw = settings.auth_oidc_providers.get_secret_value()  # type: ignore[union-attr]
        configs = [OIDCProviderConfig.model_validate(entry) for entry in json.loads(raw)]
        return {
            cfg.id: OIDCAuthProvider(cfg, session_ttl_seconds=settings.auth_session_ttl_seconds)
            for cfg in configs
        }
    raise ValueError(f"Unknown auth_mode: {settings.auth_mode!r}")


@lru_cache(maxsize=1)
def get_auth_provider() -> AuthProvider:
    """Return the *first* active provider — convenience for single-provider
    paths (``AUTH_MODE=none`` and ``=basic``, or ``=oidc`` with one entry).

    Routes that need to look up a specific provider by id under multi-
    provider OIDC use ``get_auth_providers()[provider_id]`` instead.
    """
    providers = get_auth_providers()
    if not providers:
        raise RuntimeError("no auth providers configured")
    return next(iter(providers.values()))


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
