"""Structured ``auth_event`` log emitter.

Operators query CloudWatch Logs Insights / Logfire by ``category="auth_event"``
to get a clean login/logout timeline without standing up a separate audit
store. Failure reasons are categorical (``invalid_password``,
``unknown_user``, ``provider_error``) — never the username attempted, never
the password hash, never PII.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

AuthEvent = Literal[
    "login_success",
    "login_failure",
    "logout",
    "session_expired",
    "session_invalid",
]

FailureReason = Literal[
    "unknown_user",
    "invalid_password",
    "csrf_mismatch",
    "provider_error",
    "missing_credentials",
    "rate_limited",
]


def emit(
    event: AuthEvent,
    *,
    provider_id: str,
    sub: str | None = None,
    reason: FailureReason | None = None,
    client_ip: str | None = None,
) -> None:
    """Emit a structured auth event.

    Uses ``logger.info`` for success/logout and ``logger.warning`` for
    failures so default log filters don't drop signal-of-attack events.
    The ``extra`` dict carries the structured fields; Logfire and OTel
    pick them up automatically when enabled.
    """
    extra = {
        "category": "auth_event",
        "auth_event": event,
        "provider_id": provider_id,
        "sub": sub,
        "reason": reason,
        "client_ip": client_ip,
    }
    if event in ("login_failure", "session_invalid"):
        logger.warning("auth_event: %s (%s)", event, reason or "unspecified", extra=extra)
    else:
        logger.info("auth_event: %s", event, extra=extra)
