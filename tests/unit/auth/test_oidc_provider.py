"""Unit tests for ``OIDCAuthProvider`` against a respx-mocked IdP.

Covers the OIDC contract surface: PKCE round-trip, state tampering,
expired ID token, ``aud`` mismatch, ``iss`` mismatch, nonce mismatch,
JWKS rotation handled, and provider-id binding on the tx cookie.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
import respx
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey
from pydantic import SecretStr
from src.auth.providers.oidc_provider import (
    OIDCAuthProvider,
    OIDCError,
    OIDCProviderConfig,
)

ISSUER = "https://idp.example/"
DISCOVERY_URL = f"{ISSUER}.well-known/openid-configuration"
AUTHZ = f"{ISSUER}authorize"
TOKEN_URL = f"{ISSUER}token"
JWKS_URL = f"{ISSUER}jwks"
CLIENT_ID = "client-abc"
REDIRECT_URI = "http://app.local/api/v1/auth/callback/idp"


def _discovery_doc() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHZ,
        "token_endpoint": TOKEN_URL,
        "jwks_uri": JWKS_URL,
    }


def _build_keypair() -> tuple[RSAKey, dict[str, Any]]:
    """Generate an RSA signing key + the matching JWKS the IdP would publish."""
    key = RSAKey.generate_key(2048, parameters={"alg": "RS256", "use": "sig"})
    jwks = KeySet([key]).as_dict()
    return key, jwks


def _id_token(
    key: RSAKey,
    *,
    iss: str = ISSUER,
    aud: str | list[str] = CLIENT_ID,
    nonce: str | None = "n-test",
    sub: str = "user-123",
    email: str | None = "alice@example.test",
    name: str | None = "Alice",
    exp_offset_seconds: int = 600,
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": iss,
        "aud": aud,
        "sub": sub,
        "iat": now,
        "exp": now + exp_offset_seconds,
    }
    if nonce is not None:
        claims["nonce"] = nonce
    if email is not None:
        claims["email"] = email
    if name is not None:
        claims["name"] = name
    return jwt.encode({"alg": "RS256", "kid": key.kid}, claims, key)


def _make_provider() -> OIDCAuthProvider:
    config = OIDCProviderConfig(
        id="idp",
        display_name="Sign in with IdP",
        discovery_url=DISCOVERY_URL,
        client_id=CLIENT_ID,
        client_secret=SecretStr("client-secret-shh"),
    )
    return OIDCAuthProvider(config, session_ttl_seconds=3600)


@pytest.fixture
def mock_idp() -> Any:
    key, jwks = _build_keypair()
    with respx.mock(assert_all_called=False) as respx_mock:
        respx_mock.get(DISCOVERY_URL).mock(
            return_value=httpx.Response(200, json=_discovery_doc())
        )
        respx_mock.get(JWKS_URL).mock(return_value=httpx.Response(200, json=jwks))
        respx_mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "ignored",
                    "id_token": _id_token(key),
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )
        )
        # Stash the signing key on the mock so individual tests can mint
        # alternative tokens (expired / wrong-aud / etc.) and override
        # the token route.
        respx_mock.signing_key = key  # type: ignore[attr-defined]
        respx_mock.jwks = jwks  # type: ignore[attr-defined]
        yield respx_mock


@pytest.mark.asyncio
@pytest.mark.unit
async def test_begin_authorization_builds_valid_pkce_url(mock_idp: Any) -> None:
    provider = _make_provider()
    auth_url, tx = await provider.begin_authorization(
        redirect_uri=REDIRECT_URI, next_path="/somewhere"
    )

    assert auth_url.startswith(f"{AUTHZ}?")
    # State, nonce, verifier all present and look like ≥32-char tokens.
    assert len(tx["state"]) >= 32
    assert len(tx["nonce"]) >= 32
    assert len(tx["verifier"]) >= 64
    assert tx["next_path"] == "/somewhere"
    assert tx["provider_id"] == "idp"
    # PKCE challenge is in the auth URL, not the verifier.
    assert tx["verifier"] not in auth_url
    assert "code_challenge=" in auth_url
    assert "code_challenge_method=S256" in auth_url


@pytest.mark.asyncio
@pytest.mark.unit
async def test_complete_authorization_happy_path(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    # Re-mint the id_token so its nonce matches the freshly minted tx.
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ignored",
                "id_token": _id_token(mock_idp.signing_key, nonce=tx["nonce"]),
                "token_type": "Bearer",
            },
        )
    )

    result = await provider.complete_authorization(
        code="real-code",
        state_from_query=tx["state"],
        tx_payload=tx,
        redirect_uri=REDIRECT_URI,
    )
    assert result.identity.sub == "user-123"
    assert result.identity.email == "alice@example.test"
    assert result.identity.name == "Alice"
    assert result.identity.provider_id == "idp"
    assert result.next_path == "/"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_state_tampering_rejected(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    with pytest.raises(OIDCError, match="state mismatch"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query="not-the-state",
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_provider_id_binding_rejected(mock_idp: Any) -> None:
    """A tx cookie minted for one provider must not be replayed at another."""
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    tx["provider_id"] = "different-provider"
    with pytest.raises(OIDCError, match="tx cookie does not belong"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query=tx["state"],
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_nonce_mismatch_rejected(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    # IdP returns a token with the wrong nonce.
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "x",
                "id_token": _id_token(mock_idp.signing_key, nonce="some-other-nonce"),
            },
        )
    )
    with pytest.raises(OIDCError, match="nonce mismatch"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query=tx["state"],
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_aud_mismatch_rejected(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "x",
                "id_token": _id_token(
                    mock_idp.signing_key, aud="some-other-client", nonce=tx["nonce"]
                ),
            },
        )
    )
    with pytest.raises(OIDCError, match="aud"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query=tx["state"],
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_iss_mismatch_rejected(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "x",
                "id_token": _id_token(
                    mock_idp.signing_key,
                    iss="https://impostor.example/",
                    nonce=tx["nonce"],
                ),
            },
        )
    )
    with pytest.raises(OIDCError, match="issuer"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query=tx["state"],
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_expired_id_token_rejected(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "x",
                "id_token": _id_token(
                    mock_idp.signing_key,
                    nonce=tx["nonce"],
                    exp_offset_seconds=-3600,
                ),
            },
        )
    )
    with pytest.raises(OIDCError, match="expired"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query=tx["state"],
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_aud_as_list_with_client_id_accepted(mock_idp: Any) -> None:
    """``aud`` may be an array; we accept as long as our client_id is in it."""
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "x",
                "id_token": _id_token(
                    mock_idp.signing_key,
                    aud=[CLIENT_ID, "another-resource-server"],
                    nonce=tx["nonce"],
                ),
            },
        )
    )
    result = await provider.complete_authorization(
        code="real-code",
        state_from_query=tx["state"],
        tx_payload=tx,
        redirect_uri=REDIRECT_URI,
    )
    assert result.identity.sub == "user-123"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_token_endpoint_error_surfaces(mock_idp: Any) -> None:
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "invalid_grant", "error_description": "bad code"}
        )
    )
    with pytest.raises(OIDCError, match="token exchange failed"):
        await provider.complete_authorization(
            code="real-code",
            state_from_query=tx["state"],
            tx_payload=tx,
            redirect_uri=REDIRECT_URI,
        )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_jwks_rotation_handled(mock_idp: Any) -> None:
    """If the IdP rotated keys between our cache fill and the id_token,
    the provider must refresh JWKS once and retry validation. Fixes a
    common quiet-day-long-uptime failure mode.
    """
    provider = _make_provider()
    _, tx = await provider.begin_authorization(redirect_uri=REDIRECT_URI, next_path="/")

    # Force the provider to cache the *original* JWKS first (touched
    # during begin_authorization's discovery fetch). Now simulate
    # rotation: a new key signs the id_token, and JWKS_URL serves it.
    new_key, new_jwks = _build_keypair()
    mock_idp.get(JWKS_URL).mock(return_value=httpx.Response(200, json=new_jwks))
    mock_idp.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "x",
                "id_token": _id_token(new_key, nonce=tx["nonce"]),
            },
        )
    )

    # Provider has the OLD jwks cached; validation will fail, force-refresh
    # JWKS, retry, and succeed.
    result = await provider.complete_authorization(
        code="real-code",
        state_from_query=tx["state"],
        tx_payload=tx,
        redirect_uri=REDIRECT_URI,
    )
    assert result.identity.sub == "user-123"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_login_url_returns_backend_kickoff() -> None:
    provider = _make_provider()
    url = await provider.login_url(request=None, next_path="/back/to/here")
    assert url.startswith("/api/v1/auth/login/idp")
    assert "next=/back/to/here" in url


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handle_callback_protocol_method_raises() -> None:
    """OIDC routes call ``complete_authorization`` directly; the Protocol's
    ``handle_callback`` is only meaningful for shapes that don't need
    cookie state, and would silently misuse the OIDC flow if invoked.
    """
    provider = _make_provider()
    with pytest.raises(NotImplementedError):
        await provider.handle_callback(request=None)
