"""HTTP basic auth provider — operator-provisioned via env.

``AUTH_BASIC_USERS`` is a **semicolon-separated** list of
``username:argon2hash`` pairs. Hashes are produced with
``make auth-hash-password``. There is deliberately no signup endpoint, no
user database, and no admin UI — adding or removing a user means editing
env and restarting. This matches how API keys are managed today and keeps
a locked-down deployment locked down.

Why semicolon and not comma? Argon2id hashes embed a comma-separated
parameter block (``m=…,t=…,p=…``) in the middle of the hash string. A
comma-delimited CSV would split that block and corrupt every entry. Argon2
hashes do not contain ``;`` so semicolon is unambiguous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Request

from ..base import Identity
from ..session import make_identity

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _BasicUser:
    username: str
    password_hash: str


def _parse_users(raw: str) -> dict[str, _BasicUser]:
    """Parse the env-supplied user list into a username→user map.

    Entries are separated by ``;`` (not comma — argon2 parameter blocks
    contain commas). We split on only the first colon per entry so the
    full ``$argon2…`` hash on the right is preserved.
    """
    users: dict[str, _BasicUser] = {}
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            logger.warning("AUTH_BASIC_USERS entry missing ':' separator; skipping")
            continue
        username, _, password_hash = entry.partition(":")
        username = username.strip()
        password_hash = password_hash.strip()
        if not username or not password_hash.startswith("$argon2"):
            logger.warning("AUTH_BASIC_USERS entry rejected: malformed username or hash")
            continue
        users[username] = _BasicUser(username=username, password_hash=password_hash)
    return users


class InvalidCredentials(Exception):
    """Raised when login should fail. Caller maps to a 401 with an opaque
    error so we never leak whether the username exists.
    """


class BasicAuthProvider:
    id: str = "basic"
    display_name: str = "Sign in"

    def __init__(self, users_csv: str, session_ttl_seconds: int) -> None:
        self._users = _parse_users(users_csv)
        self._hasher = PasswordHasher()
        self._session_ttl = session_ttl_seconds
        if not self._users:
            logger.warning("BasicAuthProvider initialised with zero users — no login can succeed")

    async def login_url(self, *, request: Request, next_path: str) -> str:
        # Basic mode posts directly to /api/v1/auth/login; the SPA never needs
        # to hit a provider-specific URL. Returning the SPA login route keeps
        # the /auth/config response uniform for the UI.
        return "/login"

    async def handle_callback(self, request: Request) -> Identity:
        raise NotImplementedError("BasicAuthProvider has no redirect-back flow")

    async def logout_url(self, identity: Identity) -> str | None:
        return None

    def authenticate(self, *, username: str, password: str) -> Identity:
        """Verify the password and return a fresh :class:`Identity`.

        Raises :class:`InvalidCredentials` for both unknown users and wrong
        passwords. The argon2 verify is constant-time per its library
        contract; we run it against a dummy hash on unknown-user to keep
        timing identical between the two failure modes.
        """
        user = self._users.get(username)
        if user is None:
            # Run a verify against a known-bad reference hash so timing for
            # unknown-user matches wrong-password. argon2-cffi raises on
            # mismatch — we ignore the exception, only the timing matters.
            try:
                self._hasher.verify(_DUMMY_HASH, password)
            except Exception:
                pass
            raise InvalidCredentials("invalid credentials")

        try:
            self._hasher.verify(user.password_hash, password)
        except VerifyMismatchError as exc:
            raise InvalidCredentials("invalid credentials") from exc

        return make_identity(
            sub=user.username,
            provider_id=self.id,
            ttl_seconds=self._session_ttl,
            email=None,
            name=user.username,
        )


# Pre-computed argon2id hash of the literal string ``__never_a_real_password__``.
# Used solely to keep authenticate() timing constant for unknown users. The
# value isn't sensitive — it intentionally hashes a placeholder. We compute it
# at import time so we don't pay the cost on every unknown-user request.
_DUMMY_HASH = PasswordHasher().hash("__never_a_real_password__")
