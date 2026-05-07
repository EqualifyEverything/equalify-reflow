"""Double-submit CSRF token helpers.

The session cookie is ``HttpOnly`` so JS can't read it; that defeats common
CSRF attacks for cross-origin reads, but state-changing endpoints still need
protection because a victim's cookie travels on cross-site form posts.

The pattern: at login we set a second cookie (``reflow_csrf``, NOT HttpOnly)
whose value is an HMAC of the session id. The SPA reads the cookie via
``document.cookie`` and echoes it as ``X-CSRF-Token`` on non-GET requests
under ``/api/v1/auth/*``. The server recomputes the HMAC from the session
cookie value and constant-time compares.

The OIDC flow doesn't need this — its CSRF defence is the OAuth ``state``
parameter, which is bound to the per-tx signed cookie set in
:mod:`providers.oidc_provider`.
"""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256


def make_token(session_value: str, secret_key: str) -> str:
    """Return the CSRF token to set on the companion cookie.

    The session cookie's full signed value is the input — that way rotating
    the session (login, sliding re-issue, logout) automatically rotates the
    CSRF token, and a stale token is rejected as a side effect.
    """
    return hmac.new(secret_key.encode("utf-8"), session_value.encode("utf-8"), sha256).hexdigest()


def verify(*, session_value: str, csrf_header: str | None, secret_key: str) -> bool:
    """Constant-time check. ``csrf_header`` may be ``None`` (header absent)."""
    if not csrf_header:
        return False
    expected = make_token(session_value, secret_key)
    return secrets.compare_digest(expected, csrf_header)
