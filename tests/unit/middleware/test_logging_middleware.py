"""Unit tests for request logging middleware identity extraction."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from src.auth.base import Identity
from src.middleware.logging_middleware import _identity_fields

pytestmark = pytest.mark.unit


def _request(**state_attrs):
    """Minimal stand-in for a Request: only request.state is accessed."""
    return SimpleNamespace(state=SimpleNamespace(**state_attrs))


def _identity() -> Identity:
    now = datetime.now(UTC)
    return Identity(
        sub="zach",
        email="zach@example.com",
        name="Zach",
        provider_id="basic",
        issued_at=now,
        expires_at=now,
    )


def test_anonymous_request_yields_no_fields():
    assert _identity_fields(_request()) == {}


def test_session_identity_surfaces_user_fields():
    fields = _identity_fields(_request(identity=_identity()))
    assert fields == {
        "user_sub": "zach",
        "user_email": "zach@example.com",
        "user_provider": "basic",
    }


def test_api_key_label_surfaces_without_session_identity():
    fields = _identity_fields(_request(api_key_label="daisy"))
    assert fields == {"api_key_label": "daisy"}


def test_session_and_api_key_both_surface():
    fields = _identity_fields(_request(identity=_identity(), api_key_label="svc"))
    assert fields["user_sub"] == "zach"
    assert fields["api_key_label"] == "svc"


def test_non_identity_value_is_ignored():
    # A truthy non-Identity (e.g. a MagicMock in upstream tests) must not
    # be mistaken for a real identity.
    fields = _identity_fields(_request(identity="not-an-identity", api_key_label=123))
    assert fields == {}
