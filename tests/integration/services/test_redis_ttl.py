"""Test suite for Redis TTL (time-to-live) configuration.

Tests ensure TTL values are correctly configured to prevent Redis memory exhaustion.
Note: TTL is now set atomically via Lua scripts in JobService (hset+expire+zadd).
These tests verify the configuration and TTL lookup logic, not the Redis call pattern.
"""


import pytest
from src.config import settings
from src.services.job_service import JobService
from src.shared.constants.statuses import (
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETED,
    STATUS_DENIED,
    STATUS_FAILED,
    STATUS_PII_SCANNING,
    STATUS_PROCESSING,
)


@pytest.fixture
def mock_redis_client(mocker):
    """Create mock Redis client for testing."""
    client = mocker.AsyncMock()
    return client


@pytest.fixture
def job_service(mock_redis_client):
    """Create job service with mock Redis client."""
    return JobService(redis_client=mock_redis_client)


class TestTTLConfiguration:
    """Test TTL configuration loading and defaults."""

    def test_ttl_settings_loaded(self, job_service):
        """Verify TTL settings are loaded from config."""
        assert job_service.job_ttl_active == settings.job_ttl_active
        assert job_service.job_ttl_completed == settings.job_ttl_completed
        assert job_service.job_ttl_failed == settings.job_ttl_failed
        assert job_service.job_ttl_denied == settings.job_ttl_denied

    def test_ttl_defaults_are_reasonable(self, job_service):
        """Verify default TTL values are in expected ranges."""
        # Active jobs: 7 days
        assert job_service.job_ttl_active == 7 * 24 * 3600
        # Completed/Failed jobs: 30 days
        assert job_service.job_ttl_completed == 30 * 24 * 3600
        assert job_service.job_ttl_failed == 30 * 24 * 3600
        # Denied jobs: 7 days
        assert job_service.job_ttl_denied == 7 * 24 * 3600

    def test_ttl_values_are_positive(self, job_service):
        """Verify all TTL values are positive integers."""
        assert job_service.job_ttl_active > 0
        assert job_service.job_ttl_completed > 0
        assert job_service.job_ttl_failed > 0
        assert job_service.job_ttl_denied > 0

    def test_ttl_values_are_not_infinite(self, job_service):
        """Ensure TTL is never None or -1 (infinite)."""
        assert job_service.job_ttl_active != -1
        assert job_service.job_ttl_completed != -1
        assert job_service.job_ttl_failed != -1
        assert job_service.job_ttl_denied != -1


class TestGetTTLForStatus:
    """Test _get_ttl_for_status method."""

    def test_get_ttl_for_completed_status(self, job_service):
        """Completed jobs should have 30-day TTL."""
        ttl = job_service._get_ttl_for_status(STATUS_COMPLETED)
        assert ttl == settings.job_ttl_completed
        assert ttl == 30 * 24 * 3600

    def test_get_ttl_for_failed_status(self, job_service):
        """Failed jobs should have 30-day TTL."""
        ttl = job_service._get_ttl_for_status(STATUS_FAILED)
        assert ttl == settings.job_ttl_failed
        assert ttl == 30 * 24 * 3600

    def test_get_ttl_for_denied_status(self, job_service):
        """Denied jobs should have 7-day TTL."""
        ttl = job_service._get_ttl_for_status(STATUS_DENIED)
        assert ttl == settings.job_ttl_denied
        assert ttl == 7 * 24 * 3600

    def test_get_ttl_for_active_statuses(self, job_service):
        """Active job statuses should have 7-day TTL."""
        active_statuses = [
            STATUS_PII_SCANNING,
            STATUS_AWAITING_APPROVAL,
            STATUS_PROCESSING
        ]

        for status in active_statuses:
            ttl = job_service._get_ttl_for_status(status)
            assert ttl == settings.job_ttl_active
            assert ttl == 7 * 24 * 3600

    def test_get_ttl_for_unknown_status_defaults_to_active(self, job_service):
        """Unknown statuses should default to active TTL (7 days)."""
        ttl = job_service._get_ttl_for_status("unknown_status")
        assert ttl == settings.job_ttl_active


class TestTTLConsistencyWithConfig:
    """Test TTL values stay consistent with configuration."""

    def test_ttl_active_matches_config(self, job_service):
        """Verify active TTL matches configuration."""
        assert job_service.job_ttl_active == settings.job_ttl_active

    def test_ttl_completed_matches_config(self, job_service):
        """Verify completed TTL matches configuration."""
        assert job_service.job_ttl_completed == settings.job_ttl_completed

    def test_ttl_failed_matches_config(self, job_service):
        """Verify failed TTL matches configuration."""
        assert job_service.job_ttl_failed == settings.job_ttl_failed

    def test_ttl_denied_matches_config(self, job_service):
        """Verify denied TTL matches configuration."""
        assert job_service.job_ttl_denied == settings.job_ttl_denied
