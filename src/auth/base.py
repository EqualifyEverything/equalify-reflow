"""Shared auth types: AuthMode enum, Identity model, AuthProvider Protocol."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from fastapi import Request
from pydantic import BaseModel, Field


class AuthMode(str, Enum):
    """How identities are established for incoming requests.

    ``NONE`` keeps the open default — no login, no cookies, no identity.
    ``BASIC`` checks a username/password against operator-provisioned env
    entries. ``OIDC`` runs a generic authorisation-code-with-PKCE flow against
    any IdP that publishes an OpenID Connect discovery document (Entra,
    Google, Okta, Auth0, Keycloak, ...).
    """

    NONE = "none"
    BASIC = "basic"
    OIDC = "oidc"


class Identity(BaseModel):
    """Authenticated user identity carried on ``request.state.identity``.

    Deliberately minimal: ``sub`` (stable IdP-issued user id), email, display
    name, originating provider, and the issuance/expiry pair we use for
    sliding-window cookie re-issue. Roles/groups/scopes are explicitly out of
    scope for the first cut — adding them is an additive change to this model
    plus a ``RequireGroups`` dependency.
    """

    sub: str = Field(description="Stable user identifier from the auth provider")
    email: str | None = Field(default=None, description="Verified email address, when the IdP supplies one")
    name: str | None = Field(default=None, description="Display name, when the IdP supplies one")
    provider_id: str = Field(description="Identifier of the provider that authenticated this session")
    issued_at: datetime = Field(description="When the session was minted")
    expires_at: datetime = Field(description="When the session expires absent re-issue")


@runtime_checkable
class AuthProvider(Protocol):
    """Strategy interface for authenticating a request.

    Concrete implementations live under ``src/auth/providers/``. The provider
    selected by ``settings.auth_mode`` is the one wired into routes and
    middleware at app startup.

    SSO providers serve all of ``login_url`` and ``handle_callback`` to drive
    the authorisation-code round trip. The basic provider short-circuits:
    ``login_url`` is unused (the SPA POSTs to ``/auth/login`` directly) and
    ``handle_callback`` raises. ``logout_url`` is optional — when an OIDC IdP
    advertises an ``end_session_endpoint`` we surface it so the SPA can
    optionally navigate the user there.
    """

    id: str
    """Stable identifier used in URLs (e.g. ``basic``, ``entra``, ``google``)."""

    display_name: str
    """Human-readable label rendered on the SPA login page."""

    async def login_url(self, *, request: Request, next_path: str) -> str:
        """Return the URL the SPA should navigate the user to in order to begin
        login. For OIDC this is the IdP authorisation endpoint with PKCE
        parameters. Not used by the basic provider.
        """
        ...

    async def handle_callback(self, request: Request) -> Identity:
        """Validate the IdP's redirect-back, derive an :class:`Identity`. OIDC
        only — basic raises ``NotImplementedError``.
        """
        ...

    async def logout_url(self, identity: Identity) -> str | None:
        """Optional IdP-side logout URL. Returns ``None`` for providers that
        don't advertise one (basic, IdPs without ``end_session_endpoint``).
        """
        ...
