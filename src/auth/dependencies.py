"""FastAPI ``Depends`` helpers for routes that want explicit identity access.

The session middleware already populates ``request.state.identity`` and the
API-key middleware short-circuits on it, so most endpoints never need to ask
for identity directly. These helpers exist for the ``/auth/me`` endpoint and
for any future endpoint that wants its handler to receive the identity as a
typed argument.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from .base import Identity


def current_identity(request: Request) -> Identity | None:
    """Return the authenticated identity, or ``None`` if anonymous."""
    return getattr(request.state, "identity", None)


def require_identity(request: Request) -> Identity:
    """Raise 401 if the request is anonymous."""
    identity = current_identity(request)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return identity
