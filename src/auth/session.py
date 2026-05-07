"""Signed-cookie session encoder/decoder.

Phase 1+2 use a stateless signed cookie via :mod:`itsdangerous`. The
:class:`SessionStore` Protocol is defined so a Redis-backed implementation
can be swapped in for Phase 3 with a one-line registration change in
``factory.py``; nothing else moves.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Protocol

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .base import Identity

logger = logging.getLogger(__name__)


class SessionStore(Protocol):
    """Encode/decode an :class:`Identity` to/from an opaque cookie value."""

    def encode(self, identity: Identity) -> str:
        """Serialise + sign an identity into a cookie-safe string."""
        ...

    def decode(self, value: str) -> Identity | None:
        """Verify and deserialise a cookie value. Returns ``None`` for
        tampered, expired, or otherwise invalid values — callers must treat
        ``None`` as "no session".
        """
        ...


class SignedCookieSession:
    """Stateless session: the cookie *is* the session.

    The signing key is also used as the input to ``itsdangerous`` so rotating
    it invalidates every active session — that's intentional and documented
    in the architecture explanation.
    """

    _SALT = "reflow-session-v1"

    def __init__(self, secret_key: str, max_age_seconds: int) -> None:
        if len(secret_key) < 32:
            raise ValueError("auth_secret_key must be at least 32 characters")
        self._serializer = URLSafeTimedSerializer(secret_key, salt=self._SALT)
        self._max_age = max_age_seconds

    def encode(self, identity: Identity) -> str:
        # Pydantic emits ISO-8601 for datetime; json round-trips through .model_dump_json
        return self._serializer.dumps(identity.model_dump(mode="json"))

    def decode(self, value: str) -> Identity | None:
        try:
            payload = self._serializer.loads(value, max_age=self._max_age)
        except SignatureExpired:
            return None
        except BadSignature:
            logger.warning("Session cookie failed signature verification")
            return None
        except Exception:
            # Malformed payload — never propagate; treat as anonymous.
            logger.warning("Failed to decode session cookie", exc_info=True)
            return None

        try:
            return Identity.model_validate(payload)
        except Exception:
            logger.warning("Session cookie payload failed Identity validation", exc_info=True)
            return None


def make_identity(
    *, sub: str, provider_id: str, ttl_seconds: int, email: str | None = None, name: str | None = None
) -> Identity:
    """Construct a fresh :class:`Identity` with ``issued_at``/``expires_at``
    derived from ``ttl_seconds``. Use at login and at sliding-window re-issue.
    """
    now = datetime.now(UTC)
    return Identity(
        sub=sub,
        provider_id=provider_id,
        email=email,
        name=name,
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
    )


def should_reissue(identity: Identity) -> bool:
    """Sliding-window check: re-issue once we're past the half-life."""
    now = datetime.now(UTC)
    total = (identity.expires_at - identity.issued_at).total_seconds()
    elapsed = (now - identity.issued_at).total_seconds()
    return elapsed > (total / 2.0)


__all__ = ["SessionStore", "SignedCookieSession", "make_identity", "should_reissue"]
