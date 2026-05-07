"""Session middleware: read the session cookie, stamp ``request.state.identity``.

Wired only when ``settings.auth_mode != "none"`` (see ``main.py``). Runs
ahead of ``APIKeyAuthMiddleware`` so the latter can short-circuit on
identity. Both middlewares coexist on purpose — API keys remain a parallel
auth path for programmatic clients.

The middleware never *rejects* a request on its own. It just populates
``request.state.identity`` when a valid session cookie is present and lets
``APIKeyAuthMiddleware`` (or the route's own ``Depends(require_identity)``)
make the final accept/reject decision. That separation keeps each
middleware single-purpose and makes the test matrix tractable.

It also handles sliding-window re-issue: if the session is past its
half-life and the request succeeds, we mint a fresh cookie before returning.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from ..config import settings
from .cookies import set_session_cookies
from .factory import get_session_store
from .session import make_identity, should_reissue

logger = logging.getLogger(__name__)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Read session cookie → ``request.state.identity``; re-issue when stale."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cookie_name = settings.auth_session_cookie_name
        cookie_value = request.cookies.get(cookie_name)

        identity = None
        if cookie_value:
            store = get_session_store()
            identity = store.decode(cookie_value)
            if identity is None:
                # Tampered or expired — let downstream treat the request as
                # anonymous. The browser will get the cookie cleared on the
                # response below if the request needed auth and 401s.
                logger.debug("Session cookie present but invalid; treating as anonymous")

        if identity is not None:
            request.state.identity = identity

        response = await call_next(request)

        # Sliding-window re-issue: only when the session is actually valid
        # and the request didn't itself rotate the cookie (login/logout set
        # ``response.headers["X-Auth-Cookie-Set"]`` to opt out). Avoids the
        # double-write race where logout-then-reissue resurrects a session.
        if (
            identity is not None
            and should_reissue(identity)
            and "X-Auth-Cookie-Set" not in response.headers
        ):
            fresh = make_identity(
                sub=identity.sub,
                provider_id=identity.provider_id,
                ttl_seconds=settings.auth_session_ttl_seconds,
                email=identity.email,
                name=identity.name,
            )
            set_session_cookies(response=response, identity=fresh)

        return response
