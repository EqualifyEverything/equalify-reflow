"""Unit tests for CSRF double-submit token helpers."""

from __future__ import annotations

import pytest

from src.auth import csrf

SECRET = "x" * 32


@pytest.mark.unit
def test_token_round_trip() -> None:
    session_value = "session-blob"
    token = csrf.make_token(session_value, SECRET)
    assert csrf.verify(session_value=session_value, csrf_header=token, secret_key=SECRET) is True


@pytest.mark.unit
def test_missing_header_rejected() -> None:
    assert (
        csrf.verify(session_value="session", csrf_header=None, secret_key=SECRET) is False
    )
    assert csrf.verify(session_value="session", csrf_header="", secret_key=SECRET) is False


@pytest.mark.unit
def test_mismatched_session_value_rejected() -> None:
    token = csrf.make_token("original-session", SECRET)
    assert (
        csrf.verify(session_value="different-session", csrf_header=token, secret_key=SECRET)
        is False
    )


@pytest.mark.unit
def test_wrong_token_rejected() -> None:
    token_a = csrf.make_token("session-a", SECRET)
    token_b = csrf.make_token("session-b", SECRET)
    assert csrf.verify(session_value="session-a", csrf_header=token_b, secret_key=SECRET) is False
    # Sanity — token_a does verify against session-a.
    assert (
        csrf.verify(session_value="session-a", csrf_header=token_a, secret_key=SECRET) is True
    )
