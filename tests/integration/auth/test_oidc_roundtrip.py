"""Full OIDC round-trip against the live route handlers.

The kickoff route (``/auth/login/{id}``) builds a redirect with state +
PKCE and sets the tx cookie. The callback route (``/auth/callback/{id}``)
reads the tx cookie, validates state + nonce, exchanges the code, and
mints a session cookie. Both halves are tested against a respx-mocked IdP.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

ISSUER = "http://idp.example/"
DISCOVERY_URL = f"{ISSUER}.well-known/openid-configuration"
AUTHZ = f"{ISSUER}authorize"
TOKEN_URL = f"{ISSUER}token"
JWKS_URL = f"{ISSUER}jwks"
CLIENT_ID = "client-abc"


def _discovery() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHZ,
        "token_endpoint": TOKEN_URL,
        "jwks_uri": JWKS_URL,
    }


def _key_and_jwks() -> tuple[RSAKey, dict[str, Any]]:
    key = RSAKey.generate_key(2048, parameters={"alg": "RS256", "use": "sig"})
    return key, KeySet([key]).as_dict()


def _id_token(key: RSAKey, *, nonce: str, sub: str = "user-1") -> str:
    now = int(time.time())
    return jwt.encode(
        {"alg": "RS256", "kid": key.kid},
        {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "sub": sub,
            "iat": now,
            "exp": now + 600,
            "nonce": nonce,
            "email": "alice@example.test",
            "name": "Alice",
        },
        key,
    )


@pytest.fixture
def mocked_idp() -> Any:
    key, jwks = _key_and_jwks()
    with respx.mock(assert_all_called=False) as mocker:
        mocker.get(DISCOVERY_URL).mock(return_value=httpx.Response(200, json=_discovery()))
        mocker.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks))
        mocker.signing_key = key  # type: ignore[attr-defined]
        yield mocker


@pytest.mark.integration
def test_config_lists_oidc_provider(auth_oidc_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_oidc_client.get("/api/v1/auth/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "oidc"
    assert len(body["providers"]) == 1
    p = body["providers"][0]
    assert p["id"] == "idp"
    assert p["display_name"] == "Sign in with IdP"
    assert p["login_url"].startswith("/api/v1/auth/login/idp")


@pytest.mark.integration
def test_kickoff_redirects_to_idp_and_sets_tx_cookie(  # type: ignore[no-untyped-def]
    auth_oidc_client,
    mocked_idp: Any,
) -> None:
    resp = auth_oidc_client.get(
        "/api/v1/auth/login/idp?next=/somewhere",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(AUTHZ + "?")

    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert params["client_id"] == [CLIENT_ID]
    assert params["response_type"] == ["code"]
    assert params["code_challenge_method"] == ["S256"]
    assert "code_challenge" in params
    assert params["scope"] == ["openid email profile"]
    # redirect_uri is derived from the request — TestClient defaults to
    # http://testserver, which appears in the auth URL.
    assert params["redirect_uri"][0].endswith("/api/v1/auth/callback/idp")

    # tx cookie set on the response.
    assert "reflow_oauth_tx" in {c.name for c in auth_oidc_client.cookies.jar}


@pytest.mark.integration
def test_kickoff_unknown_provider_404(auth_oidc_client) -> None:  # type: ignore[no-untyped-def]
    resp = auth_oidc_client.get(
        "/api/v1/auth/login/nope", follow_redirects=False
    )
    assert resp.status_code == 404


@pytest.mark.integration
def test_callback_full_roundtrip(  # type: ignore[no-untyped-def]
    auth_oidc_client,
    mocked_idp: Any,
) -> None:
    # 1. Kickoff — captures the auth URL (we extract state) + tx cookie.
    kickoff = auth_oidc_client.get(
        "/api/v1/auth/login/idp?next=/landing",
        follow_redirects=False,
    )
    assert kickoff.status_code == 302
    auth_params = parse_qs(urlparse(kickoff.headers["location"]).query)
    state = auth_params["state"][0]

    # 2. Decode the tx cookie to learn the nonce — without that we can't
    # mint a valid id_token. Production never decodes the tx cookie
    # client-side; this is a test-only peek using the same serializer.
    from src.auth.session import make_tx_serializer

    tx_value = auth_oidc_client.cookies["reflow_oauth_tx"]
    tx_payload = make_tx_serializer("x" * 32).loads(tx_value, max_age=600)
    nonce = tx_payload["nonce"]

    # 3. Mock the token endpoint to return an id_token whose nonce matches.
    mocked_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ignored",
                "id_token": _id_token(mocked_idp.signing_key, nonce=nonce),
                "token_type": "Bearer",
            },
        )
    )

    # 4. The IdP "redirects" the browser back to /api/v1/auth/callback/idp
    # with code+state. The tx cookie is already in the jar from kickoff.
    callback = auth_oidc_client.get(
        f"/api/v1/auth/callback/idp?code=fake-code&state={state}",
        follow_redirects=False,
    )
    assert callback.status_code == 302, callback.text
    # Lands the user on next_path.
    assert callback.headers["location"] == "/landing"

    # Session cookies set; tx cookie cleared.
    set_cookie_names = {
        h.split("=", 1)[0].strip()
        for h in callback.headers.get_list("set-cookie")
    }
    assert "reflow_session" in set_cookie_names
    assert "reflow_session_csrf" in set_cookie_names

    # 5. /auth/me with the new session cookie returns identity.
    me = auth_oidc_client.get("/api/v1/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert body["sub"] == "user-1"
    assert body["email"] == "alice@example.test"
    assert body["name"] == "Alice"
    assert body["provider_id"] == "idp"


@pytest.mark.integration
def test_callback_with_idp_error_redirects_to_login(  # type: ignore[no-untyped-def]
    auth_oidc_client,
    mocked_idp: Any,
) -> None:
    """If the IdP appends ``error=access_denied``, we shouldn't crash —
    we just clear the tx cookie and bounce the user back to /login."""
    auth_oidc_client.get("/api/v1/auth/login/idp", follow_redirects=False)
    resp = auth_oidc_client.get(
        "/api/v1/auth/callback/idp?error=access_denied&error_description=user+cancelled",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


@pytest.mark.integration
def test_callback_without_tx_cookie_400(  # type: ignore[no-untyped-def]
    auth_oidc_client,
    mocked_idp: Any,  # noqa: ARG001
) -> None:
    """Hitting /callback directly (no prior kickoff) must 400 — there's no
    state to validate against, so we must not exchange any code.
    """
    resp = auth_oidc_client.get(
        "/api/v1/auth/callback/idp?code=x&state=y", follow_redirects=False
    )
    assert resp.status_code == 400


@pytest.mark.integration
def test_callback_with_tampered_state_400(  # type: ignore[no-untyped-def]
    auth_oidc_client,
    mocked_idp: Any,
) -> None:
    auth_oidc_client.get("/api/v1/auth/login/idp", follow_redirects=False)
    # Even if we set up the token endpoint, state mismatch should reject
    # before we try to call it.
    resp = auth_oidc_client.get(
        "/api/v1/auth/callback/idp?code=x&state=tampered",
        follow_redirects=False,
    )
    assert resp.status_code == 400


@pytest.mark.integration
def test_open_redirect_attempt_falls_back_to_default_next(  # type: ignore[no-untyped-def]
    auth_oidc_client,
    mocked_idp: Any,
) -> None:
    """An attacker-controlled ``?next=https://evil.example`` must be
    sanitised to the default redirect, not propagated to the IdP and
    surfaced back as the post-login destination.
    """
    kickoff = auth_oidc_client.get(
        "/api/v1/auth/login/idp?next=https://evil.example/steal",
        follow_redirects=False,
    )
    assert kickoff.status_code == 302
    from src.auth.session import make_tx_serializer

    tx_payload = make_tx_serializer("x" * 32).loads(
        auth_oidc_client.cookies["reflow_oauth_tx"], max_age=600
    )
    assert tx_payload["next_path"] == "/"
