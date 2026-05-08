"""``/api/v1/auth/*`` router.

Always exposes ``GET /auth/config`` so the SPA can decide which login UI to
render (or whether to render one at all). The remaining routes are gated on
``settings.auth_mode != "none"``: when auth is off they 404 like any unknown
path, and the SPA's ``AuthProvider`` never calls them anyway.

Mode-conditional routes:

- ``POST /auth/login`` — basic mode only.
- ``GET /auth/login/{provider_id}`` — OIDC kickoff.
- ``GET /auth/callback/{provider_id}`` — OIDC redirect-back.
- ``POST /auth/logout`` and ``GET /auth/me`` — both basic and OIDC.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, SignatureExpired
from pydantic import BaseModel, Field

from ..config import settings
from . import audit, csrf
from .base import AuthMode, Identity
from .cookies import (
    OAUTH_TX_COOKIE_NAME,
    OAUTH_TX_TTL_SECONDS,
    clear_oauth_tx_cookie,
    clear_session_cookies,
    set_oauth_tx_cookie,
    set_session_cookies,
)
from .dependencies import require_identity
from .factory import get_auth_provider, get_auth_providers
from .providers.basic_provider import BasicAuthProvider, InvalidCredentialsError
from .providers.oidc_provider import OIDCAuthProvider, OIDCError
from .session import make_tx_serializer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


# --- Schemas -----------------------------------------------------------------


class _ProviderInfo(BaseModel):
    id: str
    display_name: str
    login_url: str


class AuthConfigResponse(BaseModel):
    """What the SPA fetches on mount to decide its login UI."""

    mode: AuthMode
    providers: list[_ProviderInfo] = Field(default_factory=list)


class BasicLoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=200)


class IdentityResponse(BaseModel):
    """Trimmed projection of :class:`Identity` for the SPA."""

    sub: str
    email: str | None = None
    name: str | None = None
    provider_id: str
    expires_at: str

    @classmethod
    def from_identity(cls, identity: Identity) -> IdentityResponse:
        return cls(
            sub=identity.sub,
            email=identity.email,
            name=identity.name,
            provider_id=identity.provider_id,
            expires_at=identity.expires_at.isoformat(),
        )


class LogoutResponse(BaseModel):
    logout_url: str | None = None


# --- Helpers -----------------------------------------------------------------


def _client_ip(request: Request) -> str | None:
    # Match APIKeyAuthMiddleware's client-IP shape so log fields are uniform.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.headers.get("X-Real-IP") or (request.client.host if request.client else None)


def _enforce_csrf(request: Request) -> None:
    """For state-changing /auth/* endpoints under basic+oidc.

    OIDC's callback uses the OAuth ``state`` parameter for CSRF (handled in
    PR2 inside the OIDC provider); this guard covers ``POST /auth/login`` and
    ``POST /auth/logout``, which set/clear cookies on a same-origin POST.

    Login is special: the user has no session cookie *yet*, so they can't
    have a CSRF cookie to echo. We therefore require an ``Origin`` header
    that matches our own scheme+host instead — browsers send Origin on
    cross-site POSTs reliably and a foreign site's Origin would fail this
    check.
    """
    origin = request.headers.get("Origin")
    if origin is None:
        # Same-origin form posts from many browsers omit Origin on
        # cross-site form-submissions; treat absence as suspicious unless
        # the request comes with Sec-Fetch-Site: same-origin (browser-controlled,
        # unforgeable from page scripts).
        if request.headers.get("Sec-Fetch-Site") != "same-origin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="missing or non-same-origin request"
            )
        return
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if origin != expected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="cross-origin request rejected"
        )


# --- Routes ------------------------------------------------------------------


@router.get("/config", response_model=AuthConfigResponse)
async def get_config(request: Request) -> AuthConfigResponse:
    """Always public. SPA polls on mount to decide whether to render login."""
    mode = AuthMode(settings.auth_mode)
    if mode is AuthMode.NONE:
        return AuthConfigResponse(mode=mode, providers=[])

    providers = get_auth_providers()
    return AuthConfigResponse(
        mode=mode,
        providers=[
            _ProviderInfo(
                id=p.id,
                display_name=p.display_name,
                login_url=await p.login_url(request=request, next_path="/"),
            )
            for p in providers.values()
        ],
    )


@router.post("/login", response_model=IdentityResponse)
async def basic_login(
    payload: BasicLoginInput,
    request: Request,
    response: Response,
) -> IdentityResponse:
    """Basic-mode login. JSON body, sets session + CSRF cookies on success."""
    if AuthMode(settings.auth_mode) is not AuthMode.BASIC:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="basic login disabled")

    _enforce_csrf(request)

    provider = get_auth_provider()
    if not isinstance(provider, BasicAuthProvider):
        # Shouldn't happen — factory + AuthMode check above guarantee this —
        # but raise loudly rather than silently.
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="provider mismatch")

    try:
        identity = provider.authenticate(username=payload.username, password=payload.password)
    except InvalidCredentialsError:
        audit.emit(
            "login_failure",
            provider_id="basic",
            reason="invalid_password",
            client_ip=_client_ip(request),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        ) from None

    set_session_cookies(response=response, identity=identity)
    audit.emit("login_success", provider_id="basic", sub=identity.sub, client_ip=_client_ip(request))
    return IdentityResponse.from_identity(identity)


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    response: Response,
    identity: Identity = Depends(require_identity),
) -> LogoutResponse:
    """Clear cookies and return any IdP-side logout URL.

    CSRF is enforced via the cookie companion (``X-CSRF-Token`` header).
    The ``X-Forwarded-Proto``/host check in :func:`_enforce_csrf` belt-and-
    braces against missing-header browsers.
    """
    _enforce_csrf(request)

    secret = settings.auth_secret_key
    if secret is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="not configured")

    # CSRF: the cookie value travels in the request; the header echoes the
    # HMAC computed from it. Recompute and compare.
    session_value = request.cookies.get(settings.auth_session_cookie_name) or ""
    header = request.headers.get("X-CSRF-Token")
    if not csrf.verify(session_value=session_value, csrf_header=header, secret_key=secret.get_secret_value()):
        audit.emit(
            "login_failure",
            provider_id=identity.provider_id,
            sub=identity.sub,
            reason="csrf_mismatch",
            client_ip=_client_ip(request),
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf mismatch")

    provider = get_auth_provider()
    logout_url = await provider.logout_url(identity)

    clear_session_cookies(response)
    audit.emit("logout", provider_id=identity.provider_id, sub=identity.sub, client_ip=_client_ip(request))
    return LogoutResponse(logout_url=logout_url)


@router.get("/me", response_model=IdentityResponse)
async def get_me(
    identity: Identity = Depends(require_identity),
) -> IdentityResponse:
    """Return the current identity, 401 if anonymous. SPA hits this on mount."""
    return IdentityResponse.from_identity(identity)


# --- OIDC kickoff + callback -------------------------------------------------


def _redirect_uri_for(request: Request, provider_id: str) -> str:
    """Build the absolute redirect_uri the IdP calls back to.

    Derived from the request's scheme + host so the same code works in
    local dev (``http://localhost:8080``) and behind an HTTPS-terminating
    ALB. Operators must register *exactly* this URL with the IdP — there
    is no wildcard support in OAuth.
    """
    return f"{request.url.scheme}://{request.url.netloc}/api/v1/auth/callback/{provider_id}"


def _safe_next_path(raw: str | None) -> str:
    """Sanitise the ``next`` query param so we only redirect to in-app
    paths. Anything starting with a scheme or ``//`` is rejected to prevent
    open-redirect abuse.
    """
    if not raw:
        return settings.auth_post_login_redirect
    if raw.startswith("//") or "://" in raw:
        return settings.auth_post_login_redirect
    if not raw.startswith("/"):
        return settings.auth_post_login_redirect
    return raw


def _get_oidc_provider(provider_id: str) -> OIDCAuthProvider:
    """Look up an OIDC provider by id, 404 if not configured for this mode."""
    if AuthMode(settings.auth_mode) is not AuthMode.OIDC:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="oidc not enabled")
    provider = get_auth_providers().get(provider_id)
    if not isinstance(provider, OIDCAuthProvider):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown provider")
    return provider


@router.get("/login/{provider_id}")
async def oidc_login(provider_id: str, request: Request, next: str = "") -> RedirectResponse:
    """OIDC kickoff. Mints state/nonce/PKCE, sets the tx cookie, redirects to IdP."""
    provider = _get_oidc_provider(provider_id)
    secret = settings.auth_secret_key
    if secret is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="not configured")

    next_path = _safe_next_path(next)
    redirect_uri = _redirect_uri_for(request, provider_id)
    auth_url, tx_payload = await provider.begin_authorization(
        redirect_uri=redirect_uri, next_path=next_path
    )

    serializer = make_tx_serializer(secret.get_secret_value())
    signed = serializer.dumps(tx_payload)

    response = RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)
    set_oauth_tx_cookie(response, signed)
    return response


@router.get("/callback/{provider_id}")
async def oidc_callback(
    provider_id: str,
    request: Request,
    response: Response,
) -> RedirectResponse:
    """OIDC redirect-back. Validates state, exchanges code, mints session."""
    provider = _get_oidc_provider(provider_id)
    secret = settings.auth_secret_key
    if secret is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="not configured")

    code = request.query_params.get("code")
    state_from_query = request.query_params.get("state")
    error_from_idp = request.query_params.get("error")

    if error_from_idp:
        # IdP refused the user (consent declined, account disabled, …).
        # Don't tell the SPA more than the category; the IdP already
        # showed the user the specifics.
        audit.emit(
            "login_failure",
            provider_id=provider_id,
            reason="provider_error",
            client_ip=_client_ip(request),
        )
        redirect = RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
        clear_oauth_tx_cookie(redirect)
        return redirect

    if not code or not state_from_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="callback missing code or state",
        )

    tx_cookie = request.cookies.get(OAUTH_TX_COOKIE_NAME)
    if not tx_cookie:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing oauth tx cookie",
        )

    serializer = make_tx_serializer(secret.get_secret_value())
    try:
        tx_payload = serializer.loads(tx_cookie, max_age=OAUTH_TX_TTL_SECONDS)
    except SignatureExpired as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="oauth tx expired") from exc
    except BadSignature as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="oauth tx invalid") from exc

    if not isinstance(tx_payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="oauth tx malformed")

    redirect_uri = _redirect_uri_for(request, provider_id)
    try:
        result = await provider.complete_authorization(
            code=code,
            state_from_query=state_from_query,
            tx_payload={k: str(v) for k, v in tx_payload.items()},
            redirect_uri=redirect_uri,
        )
    except OIDCError as exc:
        audit.emit(
            "login_failure",
            provider_id=provider_id,
            reason="provider_error",
            client_ip=_client_ip(request),
        )
        logger.warning("OIDC callback failed for provider %s: %s", provider_id, exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="authentication failed"
        ) from exc

    redirect = RedirectResponse(url=result.next_path, status_code=status.HTTP_302_FOUND)
    set_session_cookies(response=redirect, identity=result.identity)
    clear_oauth_tx_cookie(redirect)
    audit.emit(
        "login_success",
        provider_id=provider_id,
        sub=result.identity.sub,
        client_ip=_client_ip(request),
    )
    return redirect
