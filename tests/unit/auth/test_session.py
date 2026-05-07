"""Unit tests for the signed-cookie session encoder."""

from __future__ import annotations

import time

import pytest
from src.auth.session import SignedCookieSession, make_identity, should_reissue

SECRET = "x" * 32
OTHER_SECRET = "y" * 32


@pytest.mark.unit
def test_signed_cookie_round_trip() -> None:
    store = SignedCookieSession(secret_key=SECRET, max_age_seconds=3600)
    identity = make_identity(sub="alice", provider_id="basic", ttl_seconds=3600)

    encoded = store.encode(identity)
    decoded = store.decode(encoded)

    assert decoded is not None
    assert decoded.sub == identity.sub
    assert decoded.provider_id == identity.provider_id
    assert decoded.expires_at == identity.expires_at


@pytest.mark.unit
def test_short_secret_rejected() -> None:
    with pytest.raises(ValueError, match="at least 32 characters"):
        SignedCookieSession(secret_key="short", max_age_seconds=3600)


@pytest.mark.unit
def test_tampered_cookie_returns_none() -> None:
    store = SignedCookieSession(secret_key=SECRET, max_age_seconds=3600)
    identity = make_identity(sub="alice", provider_id="basic", ttl_seconds=3600)
    encoded = store.encode(identity)

    # Flip a byte in the middle. itsdangerous will reject the signature.
    tampered = encoded[:-3] + ("a" if encoded[-3] != "a" else "b") + encoded[-2:]
    assert store.decode(tampered) is None


@pytest.mark.unit
def test_wrong_secret_returns_none() -> None:
    encoder = SignedCookieSession(secret_key=SECRET, max_age_seconds=3600)
    decoder = SignedCookieSession(secret_key=OTHER_SECRET, max_age_seconds=3600)
    identity = make_identity(sub="alice", provider_id="basic", ttl_seconds=3600)

    assert decoder.decode(encoder.encode(identity)) is None


@pytest.mark.unit
def test_expired_cookie_returns_none() -> None:
    # itsdangerous timestamps are integer-second resolution; sleep > 2s to
    # guarantee we cross max_age=1 regardless of the encode-time fractional
    # offset.
    store = SignedCookieSession(secret_key=SECRET, max_age_seconds=1)
    identity = make_identity(sub="alice", provider_id="basic", ttl_seconds=1)
    encoded = store.encode(identity)
    time.sleep(2.1)
    assert store.decode(encoded) is None


@pytest.mark.unit
def test_should_reissue_after_half_life() -> None:
    fresh = make_identity(sub="alice", provider_id="basic", ttl_seconds=3600)
    assert should_reissue(fresh) is False

    # Build an aged identity that legitimately predates half-life: shift both
    # issued_at AND expires_at backward together so the total span stays at
    # 3600s. With elapsed=2000 and halflife=1800, should_reissue returns True.
    from datetime import timedelta

    shift = timedelta(seconds=2000)
    aged = fresh.model_copy(
        update={
            "issued_at": fresh.issued_at - shift,
            "expires_at": fresh.expires_at - shift,
        }
    )
    assert should_reissue(aged) is True
