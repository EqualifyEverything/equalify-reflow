"""Integration: basic-mode login → me → logout round-trip."""

from __future__ import annotations

import pytest

COMMON_HEADERS = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


@pytest.mark.integration
def test_config_reports_basic_mode(auth_basic_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_basic_client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "basic"
    assert len(body["providers"]) == 1
    assert body["providers"][0]["id"] == "basic"


@pytest.mark.integration
def test_login_succeeds_and_sets_cookies(
    auth_basic_client,  # type: ignore[no-untyped-def]
    basic_password: str,
) -> None:
    resp = auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers=COMMON_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sub"] == "alice"
    assert body["provider_id"] == "basic"
    # Both cookies present on the response.
    cookies = {c.name: c for c in auth_basic_client.cookies.jar}
    assert "reflow_session" in cookies
    assert "reflow_session_csrf" in cookies


@pytest.mark.integration
def test_login_with_wrong_password_returns_401(
    auth_basic_client,  # type: ignore[no-untyped-def]
) -> None:
    resp = auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrong"},
        headers=COMMON_HEADERS,
    )
    assert resp.status_code == 401


@pytest.mark.integration
def test_login_with_unknown_user_returns_401(
    auth_basic_client,  # type: ignore[no-untyped-def]
) -> None:
    resp = auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "anything"},
        headers=COMMON_HEADERS,
    )
    assert resp.status_code == 401


@pytest.mark.integration
def test_me_anonymous_returns_401(auth_basic_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_basic_client.get("/api/v1/auth/me")
    assert resp.status_code == 401


@pytest.mark.integration
def test_me_after_login_returns_identity(
    auth_basic_client,  # type: ignore[no-untyped-def]
    basic_password: str,
) -> None:
    auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers=COMMON_HEADERS,
    )
    resp = auth_basic_client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["sub"] == "alice"


@pytest.mark.integration
def test_logout_clears_cookies(
    auth_basic_client,  # type: ignore[no-untyped-def]
    basic_password: str,
) -> None:
    auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers=COMMON_HEADERS,
    )
    csrf_token = auth_basic_client.cookies.get("reflow_session_csrf")
    assert csrf_token

    resp = auth_basic_client.post(
        "/api/v1/auth/logout",
        headers={**COMMON_HEADERS, "X-CSRF-Token": csrf_token},
    )
    assert resp.status_code == 200, resp.text

    # /me should now 401.
    me = auth_basic_client.get("/api/v1/auth/me")
    assert me.status_code == 401


@pytest.mark.integration
def test_logout_without_csrf_rejected(
    auth_basic_client,  # type: ignore[no-untyped-def]
    basic_password: str,
) -> None:
    auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers=COMMON_HEADERS,
    )
    resp = auth_basic_client.post("/api/v1/auth/logout", headers=COMMON_HEADERS)
    assert resp.status_code == 403


@pytest.mark.integration
def test_login_cross_origin_rejected(
    auth_basic_client,  # type: ignore[no-untyped-def]
    basic_password: str,
) -> None:
    resp = auth_basic_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": basic_password},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403


@pytest.mark.integration
def test_none_mode_config_endpoint_is_minimal(
    auth_none_client,  # type: ignore[no-untyped-def]
) -> None:
    resp = auth_none_client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "none"
    assert body["providers"] == []


@pytest.mark.integration
def test_none_mode_login_disabled(auth_none_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_none_client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "anything"},
        headers=COMMON_HEADERS,
    )
    assert resp.status_code == 404
