"""Cookie set/clear helpers.

Centralised so login, logout, and the middleware's sliding-window re-issue
all set the same flags. The ``X-Auth-Cookie-Set`` response header is a
sentinel: middleware checks it before re-issuing so that logout's
``Max-Age=0`` is never overwritten by a refresh in the same response cycle.
"""

from __future__ import annotations

from fastapi import Response

from ..config import settings
from . import csrf
from .base import Identity
from .factory import get_session_store


def set_session_cookies(*, response: Response, identity: Identity) -> None:
    """Set both the signed session cookie and its CSRF companion."""
    store = get_session_store()
    secret = settings.auth_secret_key
    if secret is None:
        raise RuntimeError("auth_secret_key required to set session cookies")

    session_value = store.encode(identity)
    csrf_token = csrf.make_token(session_value, secret.get_secret_value())

    # Session cookie: HttpOnly so JS can't steal it; SameSite=Lax so the
    # OIDC redirect-back from the IdP carries it through (Strict would drop
    # the cookie on the post-redirect first request and silently break login).
    response.set_cookie(
        key=settings.auth_session_cookie_name,
        value=session_value,
        max_age=settings.auth_session_ttl_seconds,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    # CSRF companion: NOT HttpOnly — the SPA needs to read it to echo as a
    # header on non-GET requests. Same Secure/SameSite/path as the session.
    response.set_cookie(
        key=f"{settings.auth_session_cookie_name}_csrf",
        value=csrf_token,
        max_age=settings.auth_session_ttl_seconds,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="lax",
        path="/",
    )
    response.headers["X-Auth-Cookie-Set"] = "1"


def clear_session_cookies(response: Response) -> None:
    """Expire both cookies on logout."""
    for name in (
        settings.auth_session_cookie_name,
        f"{settings.auth_session_cookie_name}_csrf",
    ):
        response.set_cookie(
            key=name,
            value="",
            max_age=0,
            secure=settings.auth_cookie_secure,
            httponly=(name == settings.auth_session_cookie_name),
            samesite="lax",
            path="/",
        )
    response.headers["X-Auth-Cookie-Set"] = "1"
