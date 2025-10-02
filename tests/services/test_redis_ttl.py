"""Comprehensive test suite for Redis TTL (time-to-live) management.

Tests ensure job hashes automatically expire to prevent Redis memory exhaustion.
Verifies TTL is set correctly on job creation and updated on status transitions.
"""

import pytest
import json
from datetime import datetime, timezone

from src.services.job_service import JobService
from src.config import settings
from src.shared.constants.statuses import (
    STATUS_PII_SCANNING,
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_DENIED
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
        # Redis EXPIRE -1 means no expiration - we must avoid this
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


class TestSetJobTTL:
    """Test _set_job_ttl method."""

    @pytest.mark.asyncio
    async def test_set_ttl_calls_redis_expire(self, job_service, mock_redis_client):
        """Verify TTL is set via Redis EXPIRE command."""
        mock_redis_client.expire.return_value = 1

        await job_service._set_job_ttl("job123", STATUS_COMPLETED)

        # Verify EXPIRE was called with correct parameters
        mock_redis_client.expire.assert_called_once_with(
            f"{settings.job_status_prefix}job123",
            settings.job_ttl_completed
        )

    @pytest.mark.asyncio
    async def test_set_ttl_for_different_statuses(self, job_service, mock_redis_client):
        """Verify TTL is correctly set for different job statuses."""
        mock_redis_client.expire.return_value = 1

        test_cases = [
            (STATUS_COMPLETED, settings.job_ttl_completed),
            (STATUS_FAILED, settings.job_ttl_failed),
            (STATUS_DENIED, settings.job_ttl_denied),
            (STATUS_PROCESSING, settings.job_ttl_active),
        ]

        for status, expected_ttl in test_cases:
            mock_redis_client.expire.reset_mock()
            await job_service._set_job_ttl("job456", status)

            call_args = mock_redis_client.expire.call_args
            assert call_args.args[1] == expected_ttl

    @pytest.mark.asyncio
    async def test_set_ttl_handles_redis_error(self, job_service, mock_redis_client):
        """Verify error handling when Redis EXPIRE fails."""
        mock_redis_client.expire.side_effect = Exception("Redis connection error")

        with pytest.raises(Exception) as exc:
            await job_service._set_job_ttl("job789", STATUS_COMPLETED)

        assert "Failed to set TTL for job job789" in str(exc.value)

    @pytest.mark.asyncio
    async def test_set_ttl_never_sets_infinite(self, job_service, mock_redis_client):
        """Ensure TTL is never set to -1 (infinite)."""
        mock_redis_client.expire.return_value = 1

        # Test all possible statuses
        all_statuses = [
            STATUS_PII_SCANNING,
            STATUS_AWAITING_APPROVAL,
            STATUS_PROCESSING,
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_DENIED,
            "unknown_status"
        ]

        for status in all_statuses:
            mock_redis_client.expire.reset_mock()
            await job_service._set_job_ttl("job-test", status)

            # Verify EXPIRE was called with positive TTL (not -1)
            call_args = mock_redis_client.expire.call_args
            ttl_value = call_args.args[1]
            assert ttl_value > 0, f"TTL for status '{status}' should be positive"
            assert ttl_value != -1, f"TTL for status '{status}' should not be -1 (infinite)"


class TestCreateJobWithTTL:
    """Test job creation sets initial TTL."""

    @pytest.mark.asyncio
    async def test_create_job_sets_ttl(self, job_service, mock_redis_client):
        """Verify create_job sets TTL on new job."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        await job_service.create_job(
            job_id="job123",
            s3_key="temp/file.pdf",
            status=STATUS_PII_SCANNING
        )

        # Verify both hset and expire were called
        assert mock_redis_client.hset.call_count == 1
        assert mock_redis_client.expire.call_count == 1

        # Verify expire was called with correct TTL for pii_scanning (active)
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[0] == f"{settings.job_status_prefix}job123"
        assert expire_call.args[1] == settings.job_ttl_active

    @pytest.mark.asyncio
    async def test_create_job_with_default_status_sets_ttl(self, job_service, mock_redis_client):
        """Verify create_job with default status sets TTL."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        await job_service.create_job(
            job_id="job456",
            s3_key="temp/file.pdf"
            # status defaults to "pii_scanning"
        )

        # Verify TTL was set for default pii_scanning status
        assert mock_redis_client.expire.call_count == 1
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[1] == settings.job_ttl_active

    @pytest.mark.asyncio
    async def test_create_job_order_of_operations(self, job_service, mock_redis_client):
        """Verify hset is called before expire (order matters)."""
        call_order = []

        def track_hset(*args, **kwargs):
            call_order.append('hset')
            return 3

        def track_expire(*args, **kwargs):
            call_order.append('expire')
            return 1

        mock_redis_client.hset.side_effect = track_hset
        mock_redis_client.expire.side_effect = track_expire

        await job_service.create_job("job789", "temp/file.pdf")

        # Verify hset happens before expire
        assert call_order == ['hset', 'expire']


class TestUpdateJobStatusWithTTL:
    """Test status updates adjust TTL appropriately."""

    @pytest.mark.asyncio
    async def test_update_status_sets_ttl(self, job_service, mock_redis_client):
        """Verify update_job_status sets TTL based on new status."""
        mock_redis_client.hset.return_value = 2
        mock_redis_client.expire.return_value = 1

        await job_service.update_job_status("job123", STATUS_PROCESSING)

        # Verify both hset and expire were called
        assert mock_redis_client.hset.call_count == 1
        assert mock_redis_client.expire.call_count == 1

        # Verify expire was called with active TTL for processing status
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[1] == settings.job_ttl_active

    @pytest.mark.asyncio
    async def test_transition_to_completed_sets_long_ttl(self, job_service, mock_redis_client):
        """Verify transition to completed status sets 30-day TTL."""
        mock_redis_client.hset.return_value = 2
        mock_redis_client.expire.return_value = 1

        await job_service.update_job_status("job123", STATUS_COMPLETED)

        # Verify 30-day TTL was set
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[1] == settings.job_ttl_completed
        assert expire_call.args[1] == 30 * 24 * 3600

    @pytest.mark.asyncio
    async def test_transition_to_failed_sets_long_ttl(self, job_service, mock_redis_client):
        """Verify transition to failed status sets 30-day TTL."""
        mock_redis_client.hset.return_value = 2
        mock_redis_client.expire.return_value = 1

        await job_service.update_job_status("job456", STATUS_FAILED)

        # Verify 30-day TTL was set
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[1] == settings.job_ttl_failed
        assert expire_call.args[1] == 30 * 24 * 3600

    @pytest.mark.asyncio
    async def test_transition_to_denied_sets_short_ttl(self, job_service, mock_redis_client):
        """Verify transition to denied status sets 7-day TTL."""
        mock_redis_client.hset.return_value = 2
        mock_redis_client.expire.return_value = 1

        await job_service.update_job_status("job789", STATUS_DENIED)

        # Verify 7-day TTL was set
        expire_call = mock_redis_client.expire.call_args
        assert expire_call.args[1] == settings.job_ttl_denied
        assert expire_call.args[1] == 7 * 24 * 3600

    @pytest.mark.asyncio
    async def test_status_update_order_of_operations(self, job_service, mock_redis_client):
        """Verify hset is called before expire during status update."""
        call_order = []

        def track_hset(*args, **kwargs):
            call_order.append('hset')
            return 2

        def track_expire(*args, **kwargs):
            call_order.append('expire')
            return 1

        mock_redis_client.hset.side_effect = track_hset
        mock_redis_client.expire.side_effect = track_expire

        await job_service.update_job_status("job123", STATUS_COMPLETED)

        # Verify hset happens before expire
        assert call_order == ['hset', 'expire']


class TestJobLifecycleWithTTL:
    """Test complete job lifecycle with TTL transitions."""

    @pytest.mark.asyncio
    async def test_complete_job_lifecycle_ttl_transitions(self, job_service, mock_redis_client):
        """Test TTL changes throughout job lifecycle."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        job_id = "job-lifecycle"

        # 1. Create job (pii_scanning) - should set active TTL
        await job_service.create_job(job_id, "temp/file.pdf", STATUS_PII_SCANNING)
        assert mock_redis_client.expire.call_count == 1
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_active

        # 2. Transition to awaiting_approval - should maintain active TTL
        await job_service.update_job_status(job_id, STATUS_AWAITING_APPROVAL)
        assert mock_redis_client.expire.call_count == 2
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_active

        # 3. Transition to processing - should maintain active TTL
        await job_service.update_job_status(job_id, STATUS_PROCESSING)
        assert mock_redis_client.expire.call_count == 3
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_active

        # 4. Transition to completed - should set completed TTL (30 days)
        await job_service.update_job_status(job_id, STATUS_COMPLETED)
        assert mock_redis_client.expire.call_count == 4
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_completed

    @pytest.mark.asyncio
    async def test_job_with_pii_denial_lifecycle(self, job_service, mock_redis_client):
        """Test TTL for job that gets denied after PII scan."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        job_id = "job-denied"

        # Create job
        await job_service.create_job(job_id, "temp/file.pdf", STATUS_PII_SCANNING)

        # Transition to awaiting approval
        await job_service.update_job_status(job_id, STATUS_AWAITING_APPROVAL)

        # Denied - should set 7-day TTL
        await job_service.update_job_status(job_id, STATUS_DENIED)
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_denied

    @pytest.mark.asyncio
    async def test_job_failure_lifecycle(self, job_service, mock_redis_client):
        """Test TTL for job that fails during processing."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        job_id = "job-failed"

        # Create and start processing
        await job_service.create_job(job_id, "temp/file.pdf", STATUS_PII_SCANNING)
        await job_service.update_job_status(job_id, STATUS_PROCESSING)

        # Job fails - should set 30-day TTL (for debugging)
        await job_service.update_job_status(job_id, STATUS_FAILED)
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_failed


class TestTTLMemoryManagement:
    """Test TTL prevents Redis memory exhaustion."""

    @pytest.mark.asyncio
    async def test_all_job_operations_set_ttl(self, job_service, mock_redis_client):
        """Verify every job creation sets TTL (no memory leaks)."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        # Create 100 jobs
        for i in range(100):
            await job_service.create_job(f"job-{i}", f"temp/file-{i}.pdf")

        # Every job should have TTL set
        assert mock_redis_client.expire.call_count == 100

        # Verify all TTLs are positive (not infinite)
        for call in mock_redis_client.expire.call_args_list:
            ttl_value = call.args[1]
            assert ttl_value > 0
            assert ttl_value != -1

    @pytest.mark.asyncio
    async def test_ttl_prevents_indefinite_storage(self, job_service, mock_redis_client):
        """Ensure no job is stored indefinitely (all have expiration)."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        # Test all terminal statuses have TTL
        terminal_jobs = [
            ("completed-job", STATUS_COMPLETED),
            ("failed-job", STATUS_FAILED),
            ("denied-job", STATUS_DENIED),
        ]

        for job_id, status in terminal_jobs:
            mock_redis_client.expire.reset_mock()
            await job_service.create_job(job_id, "temp/file.pdf", status)

            # Verify TTL was set
            assert mock_redis_client.expire.call_count == 1
            ttl = mock_redis_client.expire.call_args.args[1]
            assert ttl > 0, f"Job {job_id} with status {status} must have TTL"

    @pytest.mark.asyncio
    async def test_completed_jobs_have_longest_retention(self, job_service, mock_redis_client):
        """Verify completed jobs have 30-day retention (longest for retrieval)."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        await job_service.create_job("completed-job", "temp/file.pdf", STATUS_COMPLETED)

        ttl = mock_redis_client.expire.call_args.args[1]
        assert ttl == 30 * 24 * 3600  # 30 days

    @pytest.mark.asyncio
    async def test_denied_jobs_have_shortest_retention(self, job_service, mock_redis_client):
        """Verify denied jobs have 7-day retention (shortest, decision recorded)."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        await job_service.create_job("denied-job", "temp/file.pdf", STATUS_DENIED)

        ttl = mock_redis_client.expire.call_args.args[1]
        assert ttl == 7 * 24 * 3600  # 7 days


class TestTTLEdgeCases:
    """Test edge cases and error conditions."""

    @pytest.mark.asyncio
    async def test_ttl_set_even_if_hset_returns_zero(self, job_service, mock_redis_client):
        """Verify TTL is set even if hset returns 0 (field update, not creation)."""
        mock_redis_client.hset.return_value = 0  # 0 fields created (update case)
        mock_redis_client.expire.return_value = 1

        await job_service.create_job("job123", "temp/file.pdf")

        # TTL should still be set
        assert mock_redis_client.expire.call_count == 1

    @pytest.mark.asyncio
    async def test_status_update_with_metadata_still_sets_ttl(self, job_service, mock_redis_client):
        """Verify TTL is set even when status update includes complex metadata."""
        mock_redis_client.hset.return_value = 5
        mock_redis_client.expire.return_value = 1

        await job_service.update_job_status(
            "job123",
            STATUS_COMPLETED,
            metadata={"pages": 10, "confidence": 0.95},
            result_url="https://s3.../result.html"
        )

        # Verify TTL was set despite complex update
        assert mock_redis_client.expire.call_count == 1
        assert mock_redis_client.expire.call_args.args[1] == settings.job_ttl_completed

    @pytest.mark.asyncio
    async def test_add_pii_findings_does_not_modify_ttl(self, job_service, mock_redis_client):
        """Verify add_pii_findings does not reset TTL (maintains current TTL)."""
        mock_redis_client.hset.return_value = 2

        findings = [{"entity_type": "PERSON", "score": 0.85}]
        await job_service.add_pii_findings("job123", findings)

        # Should NOT call expire (TTL maintained from previous state)
        assert mock_redis_client.expire.call_count == 0

    @pytest.mark.asyncio
    async def test_add_processing_result_does_not_modify_ttl(self, job_service, mock_redis_client):
        """Verify add_processing_result does not reset TTL."""
        mock_redis_client.hset.return_value = 4

        await job_service.add_processing_result(
            "job123",
            "https://s3.../result.html",
            0.92
        )

        # Should NOT call expire (TTL will be set when status changes to completed)
        assert mock_redis_client.expire.call_count == 0


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

    @pytest.mark.asyncio
    async def test_ttl_uses_instance_values_not_global_settings(self, job_service, mock_redis_client):
        """Verify service uses instance TTL values (allows for testing/mocking)."""
        mock_redis_client.hset.return_value = 3
        mock_redis_client.expire.return_value = 1

        # Modify instance TTL value (for testing)
        original_ttl = job_service.job_ttl_active
        job_service.job_ttl_active = 1000  # Override

        await job_service.create_job("job123", "temp/file.pdf", STATUS_PROCESSING)

        # Should use modified instance value, not global settings
        ttl = mock_redis_client.expire.call_args.args[1]
        assert ttl == 1000

        # Restore original value
        job_service.job_ttl_active = original_ttl
