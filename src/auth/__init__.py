"""Authentication package — pluggable, optional auth layer for the viewer.

When ``settings.auth_mode == "none"`` (the default) this package is dormant:
``SessionAuthMiddleware`` is not registered, no session cookies are set, and
``request.state.identity`` is never populated. The repo behaves exactly as it
did before this package existed.

When auth is on, the layer is composed of:

- :class:`AuthMode` and :class:`Identity` (``base``) — the shared types.
- :class:`AuthProvider` Protocol (``base``) — pluggable strategy contract.
- ``providers/`` — one implementation per mode (``none``, ``basic``, future
  ``oidc``). Entra is configured via the OIDC provider's ``discovery_url``;
  it is not a dedicated subclass.
- :class:`SessionAuthMiddleware` (``middleware``) — reads the session cookie
  and stamps ``request.state.identity``. Runs ahead of ``APIKeyAuthMiddleware``
  so the latter can short-circuit on identity. Both middlewares coexist by
  design: API keys remain a parallel auth path for programmatic clients.
- ``routes`` — the ``/api/v1/auth/*`` router (``config``, ``login``, ``logout``,
  ``me``). Mode-conditional registration: when ``mode == "none"`` only
  ``/auth/config`` is exposed, and it reports ``mode: "none"``.
- ``audit`` — structured ``auth_event`` log emitter.
- ``cli`` — ``hash-password`` helper used by ``make auth-hash-password``.
"""

from .base import AuthMode, AuthProvider, Identity
from .factory import get_auth_provider

__all__ = ["AuthMode", "AuthProvider", "Identity", "get_auth_provider"]
