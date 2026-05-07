"""``python -m src.auth.cli hash-password`` — operator helper for basic mode.

Prompts for a password (no echo) and prints the argon2id hash that should
be pasted into ``AUTH_BASIC_USERS`` after the username and a colon.

Wired into ``make auth-hash-password`` so operators don't have to remember
the module path.
"""

from __future__ import annotations

import getpass
import sys

from argon2 import PasswordHasher


def _hash_password() -> int:
    try:
        password = getpass.getpass("Password: ")
        confirm = getpass.getpass("Confirm:  ")
    except (EOFError, KeyboardInterrupt):
        print("aborted", file=sys.stderr)
        return 1

    if not password:
        print("empty password", file=sys.stderr)
        return 1
    if password != confirm:
        print("passwords do not match", file=sys.stderr)
        return 1

    print(PasswordHasher().hash(password))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] != "hash-password":
        print("usage: python -m src.auth.cli hash-password", file=sys.stderr)
        return 2
    return _hash_password()


if __name__ == "__main__":
    raise SystemExit(main())
