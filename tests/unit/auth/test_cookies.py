"""Pin cookie security flags on the helpers in ``src.auth.cookies``.

These flags are the security surface — ``HttpOnly``, ``SameSite=Lax``,
``Secure``, ``Max-Age``, ``Path``. The integration round-trip tests assert
*presence* of cookies but not their attributes; if a future hardening pass
flips ``Lax`` → ``Strict`` or drops ``HttpOnly`` from the session cookie,
those tests stay green even though the cookie is broken or unsafe.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import Response
from pydantic import SecretStr
from src.auth.base import Identity
from src.auth.cookies import clear_session_cookies, set_session_cookies

SESSION_COOKIE = "reflow_session"


def _identity() -> Identity:
    now = datetime.now(UTC)
    return Identity(
        sub="alice",
        email=None,
        name="alice",
        provider_id="basic",
        issued_at=now,
        expires_at=now + timedelta(seconds=3600),
    )


def _parse_cookie_directives(set_cookie_header: str) -> dict[str, str | bool]:
    """Turn a ``Set-Cookie`` value into a directive map.

    Boolean directives like ``HttpOnly`` are stored as ``True`` (case-
    insensitive); key=value directives keep their value verbatim.
    """
    parts = [p.strip() for p in set_cookie_header.split(";") if p.strip()]
    result: dict[str, str | bool] = {}
    # The first part is "name=value" — index by lower-cased key for the
    # directives that follow (HttpOnly, Secure, SameSite, Max-Age, Path).
    name, _, value = parts[0].partition("=")
    result["__name"] = name
    result["__value"] = value
    for directive in parts[1:]:
        if "=" in directive:
            key, _, val = directive.partition("=")
            result[key.lower()] = val
        else:
            result[directive.lower()] = True
    return result


@pytest.fixture
def auth_settings_overridden() -> Iterator[dict[str, Any]]:
    """Patch the global settings singleton + clear cached factories.

    Rolls back on teardown so this test file can run alongside others
    without polluting the singleton.
    """
    from src.auth import factory
    from src.config import settings as global_settings

    overrides = {
        "auth_mode": "basic",
        "auth_secret_key": SecretStr("x" * 32),
        "auth_session_cookie_name": SESSION_COOKIE,
        "auth_session_ttl_seconds": 3600,
        "auth_cookie_secure": True,
    }
    saved = {k: getattr(global_settings, k) for k in overrides}
    for k, v in overrides.items():
        object.__setattr__(global_settings, k, v)
    factory.get_session_store.cache_clear()
    factory.get_auth_provider.cache_clear()
    try:
        yield overrides
    finally:
        for k, v in saved.items():
            object.__setattr__(global_settings, k, v)
        factory.get_session_store.cache_clear()
        factory.get_auth_provider.cache_clear()


def _set_cookies(response: Response) -> dict[str, dict[str, str | bool]]:
    set_session_cookies(response=response, identity=_identity())
    headers = response.headers.getlist("set-cookie")
    return {
        _parse_cookie_directives(h)["__name"]: _parse_cookie_directives(h)
        for h in headers
        if isinstance(_parse_cookie_directives(h)["__name"], str)
    }


@pytest.mark.unit
def test_session_cookie_is_httponly(auth_settings_overridden: dict[str, Any]) -> None:
    response = Response()
    cookies = _set_cookies(response)
    session = cookies[SESSION_COOKIE]
    assert session.get("httponly") is True


@pytest.mark.unit
def test_csrf_companion_is_not_httponly(
    auth_settings_overridden: dict[str, Any],
) -> None:
    """SPA needs to read the CSRF cookie via document.cookie, so it must
    NOT be HttpOnly — that's load-bearing for double-submit CSRF."""
    response = Response()
    cookies = _set_cookies(response)
    csrf = cookies[f"{SESSION_COOKIE}_csrf"]
    assert csrf.get("httponly") is not True


@pytest.mark.unit
def test_both_cookies_use_samesite_lax(
    auth_settings_overridden: dict[str, Any],
) -> None:
    """SameSite=Lax (not Strict) — load-bearing for the OIDC redirect-back
    flow. Strict drops the cookie on the post-IdP top-level GET, silently
    breaking SSO. Documented in src/auth/oidc — pinned here."""
    response = Response()
    cookies = _set_cookies(response)
    assert cookies[SESSION_COOKIE]["samesite"].lower() == "lax"
    assert cookies[f"{SESSION_COOKIE}_csrf"]["samesite"].lower() == "lax"


@pytest.mark.unit
def test_secure_flag_tracks_setting(
    auth_settings_overridden: dict[str, Any],
) -> None:
    response = Response()
    cookies = _set_cookies(response)
    assert cookies[SESSION_COOKIE].get("secure") is True
    assert cookies[f"{SESSION_COOKIE}_csrf"].get("secure") is True


@pytest.mark.unit
def test_secure_flag_off_when_disabled(
    auth_settings_overridden: dict[str, Any],
) -> None:
    """Disabling auth_cookie_secure (e.g. for local HTTP dev) must drop
    the Secure flag from both cookies."""
    from src.config import settings

    object.__setattr__(settings, "auth_cookie_secure", False)
    response = Response()
    cookies = _set_cookies(response)
    assert cookies[SESSION_COOKIE].get("secure") is not True
    assert cookies[f"{SESSION_COOKIE}_csrf"].get("secure") is not True


@pytest.mark.unit
def test_max_age_matches_ttl(auth_settings_overridden: dict[str, Any]) -> None:
    response = Response()
    cookies = _set_cookies(response)
    assert cookies[SESSION_COOKIE]["max-age"] == "3600"
    assert cookies[f"{SESSION_COOKIE}_csrf"]["max-age"] == "3600"


@pytest.mark.unit
def test_path_is_root(auth_settings_overridden: dict[str, Any]) -> None:
    response = Response()
    cookies = _set_cookies(response)
    assert cookies[SESSION_COOKIE]["path"] == "/"
    assert cookies[f"{SESSION_COOKIE}_csrf"]["path"] == "/"


@pytest.mark.unit
def test_x_auth_cookie_set_sentinel(
    auth_settings_overridden: dict[str, Any],
) -> None:
    """The middleware reads this sentinel to suppress sliding-window
    re-issue when login/logout already rotated the cookie. Must be set
    on every cookie write; otherwise the middleware races."""
    response = Response()
    set_session_cookies(response=response, identity=_identity())
    assert response.headers.get("X-Auth-Cookie-Set") == "1"


@pytest.mark.unit
def test_clear_zeroes_max_age_and_sets_sentinel(
    auth_settings_overridden: dict[str, Any],
) -> None:
    response = Response()
    clear_session_cookies(response)
    headers = response.headers.getlist("set-cookie")
    cookies = {
        _parse_cookie_directives(h)["__name"]: _parse_cookie_directives(h)
        for h in headers
    }
    assert cookies[SESSION_COOKIE]["max-age"] == "0"
    assert cookies[f"{SESSION_COOKIE}_csrf"]["max-age"] == "0"
    # Session cookie still HttpOnly during clear (so a JS read can't see
    # the empty value either); CSRF companion still NOT HttpOnly for symmetry.
    assert cookies[SESSION_COOKIE].get("httponly") is True
    assert cookies[f"{SESSION_COOKIE}_csrf"].get("httponly") is not True
    # Sentinel set so the sliding-window middleware doesn't resurrect.
    assert response.headers.get("X-Auth-Cookie-Set") == "1"
