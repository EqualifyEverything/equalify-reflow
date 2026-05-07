"""Generic OIDC provider — Microsoft Entra is just a config preset.

Implements the authorisation-code flow with PKCE against any IdP that
publishes an OpenID Connect discovery document. The discovery URL is the
only thing that distinguishes one provider from another at the code
level — Entra is ``https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration``,
Google is ``https://accounts.google.com/.well-known/openid-configuration``,
Okta is ``https://{domain}/.well-known/openid-configuration``, and so on.

ID-token validation covers the OWASP-recommended set: signature against
the IdP's JWKS (rotated keys handled), ``iss`` matches the discovery
document's issuer, ``aud`` includes our ``client_id``, ``exp`` not in the
past, ``nonce`` matches the value we minted on the kickoff.

CSRF for the redirect-back is the OAuth ``state`` parameter, bound to a
short-lived signed cookie (``reflow_oauth_tx``). Same cookie carries the
PKCE verifier and the original ``next`` path so the callback route can
finish the flow without round-tripping any state through the IdP.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from joserfc import jwt
from joserfc.jwk import KeySet
from pydantic import BaseModel, Field, SecretStr

from ..base import Identity
from ..session import make_identity

logger = logging.getLogger(__name__)


# 10 minutes: long enough that a slow IdP (Entra MFA prompts, password
# resets) still completes; short enough that a captured tx cookie can't be
# used to mount a state-replay attack hours later.
OAUTH_TX_TTL_SECONDS = 600

# Time skew allowance for ``exp`` and ``nbf`` claims — IdP and our clock
# can drift by a few seconds even with NTP. 60s is conservative.
JWT_LEEWAY_SECONDS = 60

# JWKS cache TTL. IdPs rotate keys infrequently (Entra ≈ daily). On a
# signature failure we always force-refresh once before giving up, so
# this is just an optimisation.
JWKS_CACHE_TTL_SECONDS = 3600


class OIDCProviderConfig(BaseModel):
    """One entry from ``AUTH_OIDC_PROVIDERS``.

    The shape mirrors what ``Settings._validate_auth`` already enforces at
    startup, but we re-validate here so a hand-constructed test fixture
    surfaces the same errors a misconfigured operator would see.
    """

    id: str = Field(min_length=1, description="Stable identifier used in URLs (e.g. 'entra').")
    display_name: str = Field(min_length=1, description="Label shown on the SPA login page.")
    discovery_url: str = Field(
        min_length=1,
        description="OpenID Connect discovery document URL — the IdP serves "
        "everything else (authorization_endpoint, token_endpoint, jwks_uri, issuer).",
    )
    client_id: str = Field(min_length=1)
    client_secret: SecretStr
    scopes: str = Field(default="openid email profile")


@dataclass(frozen=True, slots=True)
class CallbackResult:
    """What ``OIDCAuthProvider.complete_authorization`` returns to the
    callback route. Keeping ``next_path`` next to ``identity`` lets the
    route finish the round-trip with one decoder pass over the tx cookie.
    """

    identity: Identity
    next_path: str


class OIDCError(Exception):
    """Base class for OIDC-specific failures. Caller maps to a 400/500/502
    based on whether the failure is upstream (IdP), local (config), or
    user-controllable (tampered state, replayed nonce).
    """


def build_pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` per RFC 7636."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class OIDCAuthProvider:
    """Authorisation-code-with-PKCE OIDC provider.

    One instance per configured provider. Stateless across requests —
    discovery + JWKS caches are kept in-process for performance, but a
    cache miss just causes a refetch; nothing is persisted.
    """

    def __init__(
        self,
        config: OIDCProviderConfig,
        *,
        session_ttl_seconds: int,
        http_client_factory: Any | None = None,
    ) -> None:
        self.config = config
        self.id = config.id
        self.display_name = config.display_name
        self._session_ttl = session_ttl_seconds
        # Tests inject ``respx``-mocked AsyncClient. Production passes None
        # and we build a fresh httpx.AsyncClient per HTTP call.
        self._http_factory = http_client_factory or httpx.AsyncClient

        self._discovery: dict[str, Any] | None = None
        self._discovery_fetched_at: float = 0.0
        self._jwks: KeySet | None = None
        self._jwks_fetched_at: float = 0.0

    # ------------------------------------------------------------------ AuthProvider Protocol

    async def login_url(self, *, request: Any, next_path: str) -> str:  # noqa: ARG002
        """Where the SPA should navigate the user to begin login.

        Returns the backend kickoff route, not the IdP authorisation URL.
        The kickoff route mints state/nonce/PKCE, sets the tx cookie, and
        302s to the IdP. Doing the redirect server-side keeps the cookie
        write atomic with the redirect.
        """
        from urllib.parse import quote

        return f"/api/v1/auth/login/{self.id}?next={quote(next_path, safe='/')}"

    async def handle_callback(self, request: Any) -> Identity:  # noqa: ARG002
        """Routes call ``complete_authorization`` directly so they can also
        recover ``next_path`` from the tx cookie. This Protocol method is
        not used for OIDC and raises to make the misuse loud.
        """
        raise NotImplementedError(
            "OIDC routes call complete_authorization() directly; handle_callback "
            "is the basic-mode shape and should not be invoked for OIDC."
        )

    async def logout_url(self, identity: Identity) -> str | None:  # noqa: ARG002
        """IdP-side logout URL. PR3 wires ``end_session_endpoint`` from the
        discovery document; PR2 returns ``None`` so the SPA just clears
        local cookies.
        """
        return None

    # ------------------------------------------------------------------ OIDC-specific surface

    async def begin_authorization(
        self, *, redirect_uri: str, next_path: str
    ) -> tuple[str, dict[str, str]]:
        """Build the IdP authorisation URL + the tx payload to put in the
        signed cookie.

        Returns ``(auth_url, tx_payload)``. The route handler signs and
        stores ``tx_payload`` in the ``reflow_oauth_tx`` cookie, then 302s
        the browser to ``auth_url``.
        """
        discovery = await self._get_discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier, challenge = build_pkce_pair()

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.config.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        auth_url = f"{discovery['authorization_endpoint']}?{urlencode(params)}"

        tx_payload = {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "next_path": next_path,
            "provider_id": self.id,
        }
        return auth_url, tx_payload

    async def complete_authorization(
        self,
        *,
        code: str,
        state_from_query: str,
        tx_payload: dict[str, str],
        redirect_uri: str,
    ) -> CallbackResult:
        """Validate the redirect-back, exchange the code, and return the
        :class:`Identity` we'll mint a session from.

        Validates state (CSRF), exchanges the code for tokens (with PKCE
        verifier), validates the ID token (signature, iss, aud, exp,
        nonce). Any failure raises :class:`OIDCError` so the route can
        surface a 4xx/5xx to the browser.
        """
        if tx_payload.get("provider_id") != self.id:
            raise OIDCError("tx cookie does not belong to this provider")
        expected_state = tx_payload.get("state")
        if not expected_state or not secrets.compare_digest(expected_state, state_from_query):
            raise OIDCError("state mismatch")

        discovery = await self._get_discovery()
        token_response = await self._exchange_code(
            token_endpoint=discovery["token_endpoint"],
            code=code,
            verifier=tx_payload["verifier"],
            redirect_uri=redirect_uri,
        )

        id_token = token_response.get("id_token")
        if not id_token:
            raise OIDCError("token response missing id_token")

        claims = await self._validate_id_token(
            id_token=id_token,
            issuer=discovery["issuer"],
            expected_nonce=tx_payload.get("nonce", ""),
        )

        identity = make_identity(
            sub=str(claims["sub"]),
            provider_id=self.id,
            ttl_seconds=self._session_ttl,
            email=claims.get("email"),
            name=claims.get("name") or claims.get("preferred_username"),
        )
        return CallbackResult(identity=identity, next_path=tx_payload.get("next_path", "/"))

    # ------------------------------------------------------------------ Internals

    async def _get_discovery(self) -> dict[str, Any]:
        if self._discovery is not None and time.time() - self._discovery_fetched_at < JWKS_CACHE_TTL_SECONDS:
            return self._discovery
        async with self._http_factory(timeout=10.0) as client:
            resp = await client.get(self.config.discovery_url)
        if resp.status_code != 200:
            raise OIDCError(f"discovery fetch failed: HTTP {resp.status_code}")
        doc = resp.json()
        for required in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
            if required not in doc:
                raise OIDCError(f"discovery doc missing '{required}'")
        self._discovery = doc
        self._discovery_fetched_at = time.time()
        return doc

    async def _get_jwks(self, *, force_refresh: bool = False) -> KeySet:
        if (
            not force_refresh
            and self._jwks is not None
            and time.time() - self._jwks_fetched_at < JWKS_CACHE_TTL_SECONDS
        ):
            return self._jwks
        discovery = await self._get_discovery()
        async with self._http_factory(timeout=10.0) as client:
            resp = await client.get(discovery["jwks_uri"])
        if resp.status_code != 200:
            raise OIDCError(f"jwks fetch failed: HTTP {resp.status_code}")
        self._jwks = KeySet.import_key_set(resp.json())
        self._jwks_fetched_at = time.time()
        return self._jwks

    async def _exchange_code(
        self, *, token_endpoint: str, code: str, verifier: str, redirect_uri: str
    ) -> dict[str, Any]:
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret.get_secret_value(),
            "code_verifier": verifier,
        }
        async with self._http_factory(timeout=10.0) as client:
            resp = await client.post(
                token_endpoint,
                data=body,
                headers={"Accept": "application/json"},
            )
        if resp.status_code != 200:
            # Surface the IdP's error description if it included one — helps
            # ops triage misconfigured client secrets / redirect URIs.
            try:
                payload = resp.json()
                msg = payload.get("error_description") or payload.get("error") or resp.text
            except Exception:
                msg = resp.text
            raise OIDCError(f"token exchange failed (HTTP {resp.status_code}): {msg}")
        return resp.json()

    async def _validate_id_token(
        self, *, id_token: str, issuer: str, expected_nonce: str
    ) -> dict[str, Any]:
        keyset = await self._get_jwks()
        try:
            decoded = jwt.decode(id_token, keyset)
        except Exception as exc:
            # Try once with a forced JWKS refresh — handles the common case
            # where the IdP rotated keys between cache fills.
            keyset = await self._get_jwks(force_refresh=True)
            try:
                decoded = jwt.decode(id_token, keyset)
            except Exception as inner:
                raise OIDCError(f"id_token signature invalid: {inner}") from exc

        claims = decoded.claims
        now = int(time.time())

        if claims.get("iss") != issuer:
            raise OIDCError("id_token issuer mismatch")

        # ``aud`` may be a string or a list; client_id must be in there.
        aud = claims.get("aud")
        if isinstance(aud, str):
            aud_list = [aud]
        elif isinstance(aud, list):
            aud_list = aud
        else:
            raise OIDCError("id_token aud claim missing or malformed")
        if self.config.client_id not in aud_list:
            raise OIDCError("id_token aud does not include client_id")

        exp = claims.get("exp")
        if not isinstance(exp, int) or exp + JWT_LEEWAY_SECONDS < now:
            raise OIDCError("id_token expired")

        nbf = claims.get("nbf")
        if isinstance(nbf, int) and nbf - JWT_LEEWAY_SECONDS > now:
            raise OIDCError("id_token not yet valid")

        if expected_nonce and claims.get("nonce") != expected_nonce:
            raise OIDCError("id_token nonce mismatch")

        if "sub" not in claims:
            raise OIDCError("id_token missing sub claim")

        return claims
