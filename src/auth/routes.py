"""``/api/v1/auth/*`` router.

Always exposes ``GET /auth/config`` so the SPA can decide which login UI to
render (or whether to render one at all). The remaining routes are gated on
``settings.auth_mode != "none"``: when auth is off they 404 like any unknown
path, and the SPA's ``AuthProvider`` never calls them anyway.

OIDC routes (``/auth/login/{provider_id}``, ``/auth/callback/{provider_id}``)
land in PR2; this PR ships the basic + shared surface.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from ..config import settings
from . import audit, csrf
from .base import AuthMode, Identity
from .cookies import clear_session_cookies, set_session_cookies
from .dependencies import require_identity
from .factory import get_auth_provider
from .providers.basic_provider import BasicAuthProvider, InvalidCredentialsError

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

    provider = get_auth_provider()
    return AuthConfigResponse(
        mode=mode,
        providers=[
            _ProviderInfo(
                id=provider.id,
                display_name=provider.display_name,
                # Basic mode points at the SPA route; OIDC will return
                # /api/v1/auth/login/{id} from the provider.
                login_url=await provider.login_url(request=request, next_path="/"),
            )
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
