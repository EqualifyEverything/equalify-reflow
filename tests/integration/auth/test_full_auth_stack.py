"""Both auth paths coexist end-to-end.

Full middleware stack (``SessionAuthMiddleware`` + ``APIKeyAuthMiddleware``)
in production order. Asserts:

- Anonymous requests are rejected.
- An ``X-API-Key`` alone authenticates the request (parallel programmatic
  path).
- A session cookie alone authenticates the request (browser path).
- Both together still succeed (session takes precedence; api-key short-
  circuit doesn't *consume* the cookie).
- Same-origin shortcut is gated off when ``AUTH_MODE != "none"``: a
  browser-shaped request without any credential must still 401.
"""

from __future__ import annotations

import pytest

COMMON_HEADERS = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


@pytest.mark.integration
def test_anonymous_request_rejected(auth_full_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_full_client.get("/api/v1/test/ping")
    assert resp.status_code == 401


@pytest.mark.integration
def test_api_key_alone_authenticates(auth_full_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_full_client.get(
        "/api/v1/test/ping",
        headers={"X-API-Key": "test-api-key-1"},
    )
    assert resp.status_code == 200, resp.text
    # API-key auth doesn't establish a session identity.
    assert resp.json()["user"] is None


@pytest.mark.integration
def test_session_cookie_alone_authenticates(  # type: ignore[no-untyped-def]
    auth_full_client,
    basic_password: str,
) -> None:
    login = auth_full_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers=COMMON_HEADERS,
    )
    assert login.status_code == 200, login.text

    resp = auth_full_client.get("/api/v1/test/ping")
    assert resp.status_code == 200
    assert resp.json()["user"] == "alice"


@pytest.mark.integration
def test_session_and_api_key_both_succeed(  # type: ignore[no-untyped-def]
    auth_full_client,
    basic_password: str,
) -> None:
    auth_full_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers=COMMON_HEADERS,
    )
    resp = auth_full_client.get(
        "/api/v1/test/ping",
        headers={"X-API-Key": "test-api-key-1"},
    )
    assert resp.status_code == 200
    # Session middleware ran first and stamped identity; api-key
    # middleware short-circuited on it without touching the header.
    assert resp.json()["user"] == "alice"


@pytest.mark.integration
def test_same_origin_shortcut_disabled_when_auth_on(  # type: ignore[no-untyped-def]
    auth_full_client,
) -> None:
    """When AUTH_MODE != none, a browser-shaped same-origin request without
    a session cookie or API key must still be rejected. Otherwise the
    pre-existing same-origin bypass would silently defeat the new gate.
    """
    resp = auth_full_client.get(
        "/api/v1/test/ping",
        headers={"Sec-Fetch-Site": "same-origin"},
    )
    assert resp.status_code == 401


@pytest.mark.integration
def test_invalid_api_key_rejected_when_auth_on(  # type: ignore[no-untyped-def]
    auth_full_client,
) -> None:
    resp = auth_full_client.get(
        "/api/v1/test/ping",
        headers={"X-API-Key": "nope-not-real"},
    )
    assert resp.status_code == 401
