"""APIKeyAuthMiddleware short-circuits on a real :class:`Identity`.

This pins the security contract: when ``SessionAuthMiddleware`` has
populated ``request.state.identity`` with a real ``Identity`` object, the
api-key middleware treats the request as authenticated. Crucially, a
``MagicMock`` (or any other truthy non-Identity value) must NOT satisfy
the check — earlier iterations of the middleware used a truthy guard and
silently disabled api-key auth in mock-based tests.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi import Request
from src.auth.base import Identity
from src.middleware.api_key_auth import APIKeyAuthMiddleware


def _make_request(*, identity: object | None) -> Request:
    """Build a synthetic Request hitting an /api/* path with no API key
    header and no same-origin signals — so the only thing that can satisfy
    ``_is_public_endpoint`` is the identity short-circuit.
    """
    req = MagicMock(spec=Request)
    req.url.path = "/api/v1/feedback/config"
    req.headers = MagicMock()
    req.headers.get = MagicMock(return_value=None)
    req.query_params = {}
    state = MagicMock()
    state.identity = identity
    req.state = state
    return req


def _real_identity() -> Identity:
    now = datetime.now(UTC)
    return Identity(
        sub="alice",
        email=None,
        name="alice",
        provider_id="basic",
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )


@pytest.fixture
def middleware() -> APIKeyAuthMiddleware:
    return APIKeyAuthMiddleware(MagicMock())


@pytest.mark.unit
def test_real_identity_short_circuits(middleware: APIKeyAuthMiddleware) -> None:
    request = _make_request(identity=_real_identity())
    assert middleware._is_public_endpoint(request) is True


@pytest.mark.unit
def test_no_identity_does_not_short_circuit(middleware: APIKeyAuthMiddleware) -> None:
    request = _make_request(identity=None)
    assert middleware._is_public_endpoint(request) is False


@pytest.mark.unit
def test_magicmock_identity_does_not_short_circuit(
    middleware: APIKeyAuthMiddleware,
) -> None:
    """MagicMock attributes are always truthy. If the middleware ever drops
    its ``isinstance(_, Identity)`` check, this test fails immediately —
    silent-bypass regression caught before it ships.
    """
    request = _make_request(identity=MagicMock())
    assert middleware._is_public_endpoint(request) is False


@pytest.mark.unit
def test_identity_lookalike_does_not_short_circuit(
    middleware: APIKeyAuthMiddleware,
) -> None:
    """A class with the right attribute names but wrong type must not pass.
    Reinforces that we ``isinstance``-check, not duck-type."""

    class FakeIdentity:
        sub = "alice"
        email = None
        name = "alice"
        provider_id = "basic"

    request = _make_request(identity=FakeIdentity())
    assert middleware._is_public_endpoint(request) is False
