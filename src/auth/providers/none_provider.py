"""No-op provider: keeps the OSS default frictionless.

Selected when ``settings.auth_mode == "none"``. The middleware is not wired
when this provider is active, so its methods only run if something
mistakenly registers it as an active provider — they raise loudly to surface
that bug rather than silently letting requests through unauthenticated.
"""

from __future__ import annotations

from fastapi import Request

from ..base import Identity


class NoneAuthProvider:
    id: str = "none"
    display_name: str = "No authentication"

    async def login_url(self, *, request: Request, next_path: str) -> str:
        raise NotImplementedError("NoneAuthProvider has no login flow")

    async def handle_callback(self, request: Request) -> Identity:
        raise NotImplementedError("NoneAuthProvider has no callback flow")

    async def logout_url(self, identity: Identity) -> str | None:
        return None
