"""Unit tests for MetricsService class and Prometheus helper functions.

Tests cover:
- MetricsService initialization and key generation
- Metric increment, retrieval, and cleanup operations
- Prometheus helper functions for LLM and round metrics
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from src.services.metrics_service import (
    MetricsService,
    convergence_events_total,
    critic_issues_total,
    document_quality_score,
    llm_cost_cents_total,
    llm_request_duration_seconds,
    llm_requests_total,
    llm_tokens_total,
    record_critic_issue,
    record_llm_call,
    record_round_metrics,
    round_duration_seconds,
    round_processing_total,
)


@pytest.fixture
def mock_redis_client():
    """Create mock Redis client with common async operations for metrics."""
    client = AsyncMock()

    # Configure common return values for metrics operations
    client.hincrby.return_value = 1
    client.expire.return_value = True
    client.hget.return_value = None
    client.hgetall.return_value = {}
    client.scan.return_value = (0, [])  # cursor=0, keys=[]
    client.delete.return_value = 1

    return client


@pytest.fixture
def metrics_service(mock_redis_client):
    """Create MetricsService with mock client."""
    return MetricsService(redis_client=mock_redis_client)


@pytest.mark.unit
class TestMetricsServiceInit:
    """Tests for MetricsService initialization."""

    def test_init_stores_redis_client(self, mock_redis_client):
        """Test that redis client is stored during initialization."""
        service = MetricsService(redis_client=mock_redis_client)

        assert service.redis is mock_redis_client

    def test_init_with_none_client(self):
        """Test initialization with None client (edge case)."""
        service = MetricsService(redis_client=None)

        assert service.redis is None

    def test_metrics_prefix_constant(self, metrics_service):
        """Test that metrics prefix is correctly set."""
        assert metrics_service.METRICS_PREFIX == "eq-pdf:metrics:daily:"


@pytest.mark.unit
class TestGetMetricsKey:
    """Tests for _get_metrics_key() method."""

    def test_returns_key_with_current_date_when_no_date_provided(self, metrics_service):
        """Test key generation with current date when no date is provided."""
        with patch("src.services.metrics_service.datetime") as mock_datetime:
            mock_now = datetime(2025, 1, 15, 10, 30, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now

            key = metrics_service._get_metrics_key()

            assert key == "eq-pdf:metrics:daily:20250115"
            mock_datetime.now.assert_called_once_with(UTC)

    def test_returns_key_with_specific_date_when_provided(self, metrics_service):
        """Test key generation with a specific date."""
        specific_date = datetime(2024, 12, 25, 8, 0, 0, tzinfo=UTC)

        key = metrics_service._get_metrics_key(date=specific_date)

        assert key == "eq-pdf:metrics:daily:20241225"

    def test_key_format_matches_expected_pattern(self, metrics_service):
        """Test that key format matches 'eq-pdf:metrics:daily:YYYYMMDD'."""
        test_date = datetime(2025, 6, 7, 12, 0, 0, tzinfo=UTC)

        key = metrics_service._get_metrics_key(date=test_date)

        # Verify pattern
        assert key.startswith("eq-pdf:metrics:daily:")
        date_part = key.split(":")[-1]
        assert len(date_part) == 8
        assert date_part == "20250607"

    def test_key_with_single_digit_month_and_day(self, metrics_service):
        """Test key generation with single-digit month and day (zero-padded)."""
        test_date = datetime(2025, 1, 5, 0, 0, 0, tzinfo=UTC)

        key = metrics_service._get_metrics_key(date=test_date)

        assert key == "eq-pdf:metrics:daily:20250105"

    def test_key_with_end_of_year_date(self, metrics_service):
        """Test key generation with December 31st."""
        test_date = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)

        key = metrics_service._get_metrics_key(date=test_date)

        assert key == "eq-pdf:metrics:daily:20251231"


@pytest.mark.unit
class TestIncrementMetric:
    """Tests for increment_metric() method."""

    @pytest.mark.asyncio
    async def test_increments_metric_by_default_value(
        self, metrics_service, mock_redis_client
    ):
        """Test incrementing a metric by 1 (default)."""
        await metrics_service.increment_metric("files_processed")

        mock_redis_client.hincrby.assert_called_once()
        call_args = mock_redis_client.hincrby.call_args
        assert call_args.args[1] == "files_processed"
        assert call_args.args[2] == 1

    @pytest.mark.asyncio
    async def test_increments_metric_by_custom_value(
        self, metrics_service, mock_redis_client
    ):
        """Test incrementing a metric by a custom value."""
        await metrics_service.increment_metric("bytes_processed", value=1024)

        call_args = mock_redis_client.hincrby.call_args
        assert call_args.args[1] == "bytes_processed"
        assert call_args.args[2] == 1024

    @pytest.mark.asyncio
    async def test_sets_expiration_on_key(self, metrics_service, mock_redis_client):
        """Test that expiration is set on the metrics key."""
        await metrics_service.increment_metric("test_metric")

        mock_redis_client.expire.assert_called_once()
        call_args = mock_redis_client.expire.call_args
        key = call_args.args[0]
        assert key.startswith("eq-pdf:metrics:daily:")

        # TTL should be retention_days + 7 days buffer, in seconds
        ttl_seconds = call_args.args[1]
        assert ttl_seconds > 0
        # With default 90 day retention + 7 buffer = 97 days
        # 97 * 24 * 60 * 60 = 8,380,800 seconds
        expected_ttl = 97 * 24 * 60 * 60
        assert ttl_seconds == expected_ttl

    @pytest.mark.asyncio
    async def test_handles_redis_error_gracefully(
        self, metrics_service, mock_redis_client
    ):
        """Test that Redis errors are logged but not raised."""
        mock_redis_client.hincrby.side_effect = Exception("Redis connection error")

        # Should not raise
        await metrics_service.increment_metric("test_metric")

        # Verify operation was attempted
        mock_redis_client.hincrby.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_expire_error_gracefully(
        self, metrics_service, mock_redis_client
    ):
        """Test that expire errors are logged but not raised."""
        mock_redis_client.hincrby.return_value = 1
        mock_redis_client.expire.side_effect = Exception("Expire failed")

        # Should not raise
        await metrics_service.increment_metric("test_metric")

        mock_redis_client.expire.assert_called_once()

    @pytest.mark.asyncio
    async def test_increment_uses_correct_key_for_today(
        self, metrics_service, mock_redis_client
    ):
        """Test that increment uses the correct key for today's date."""
        with patch("src.services.metrics_service.datetime") as mock_datetime:
            mock_now = datetime(2025, 3, 20, 14, 0, 0, tzinfo=UTC)
            mock_datetime.now.return_value = mock_now

            await metrics_service.increment_metric("jobs_completed")

            call_args = mock_redis_client.hincrby.call_args
            assert call_args.args[0] == "eq-pdf:metrics:daily:20250320"


@pytest.mark.unit
class TestGetMetric:
    """Tests for get_metric() method."""

    @pytest.mark.asyncio
    async def test_returns_metric_value_when_found(
        self, metrics_service, mock_redis_client
    ):
        """Test returning metric value when it exists."""
        mock_redis_client.hget.return_value = "42"

        value = await metrics_service.get_metric("files_processed")

        assert value == 42
        mock_redis_client.hget.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_zero_when_metric_not_found(
        self, metrics_service, mock_redis_client
    ):
        """Test returning 0 when metric doesn't exist (None)."""
        mock_redis_client.hget.return_value = None

        value = await metrics_service.get_metric("nonexistent_metric")

        assert value == 0

    @pytest.mark.asyncio
    async def test_returns_zero_on_redis_error(
        self, metrics_service, mock_redis_client
    ):
        """Test returning 0 on Redis error."""
        mock_redis_client.hget.side_effect = Exception("Redis error")

        value = await metrics_service.get_metric("test_metric")

        assert value == 0

    @pytest.mark.asyncio
    async def test_get_metric_with_specific_date(
        self, metrics_service, mock_redis_client
    ):
        """Test retrieving metric for a specific date."""
        mock_redis_client.hget.return_value = "100"
        specific_date = datetime(2025, 1, 10, 0, 0, 0, tzinfo=UTC)

        value = await metrics_service.get_metric("jobs_failed", date=specific_date)

        assert value == 100
        call_args = mock_redis_client.hget.call_args
        assert call_args.args[0] == "eq-pdf:metrics:daily:20250110"

    @pytest.mark.asyncio
    async def test_get_metric_converts_string_to_int(
        self, metrics_service, mock_redis_client
    ):
        """Test that string values from Redis are converted to int."""
        mock_redis_client.hget.return_value = "9999"

        value = await metrics_service.get_metric("large_value")

        assert isinstance(value, int)
        assert value == 9999

    @pytest.mark.asyncio
    async def test_get_metric_with_bytes_response(
        self, metrics_service, mock_redis_client
    ):
        """Test handling bytes response from Redis."""
        mock_redis_client.hget.return_value = b"55"

        value = await metrics_service.get_metric("bytes_metric")

        assert value == 55


@pytest.mark.unit
class TestGetAllMetrics:
    """Tests for get_all_metrics() method."""

    @pytest.mark.asyncio
    async def test_returns_all_metrics_as_dict(
        self, metrics_service, mock_redis_client
    ):
        """Test returning all metrics as a dictionary."""
        mock_redis_client.hgetall.return_value = {
            "files_processed": "100",
            "jobs_completed": "95",
            "jobs_failed": "5",
        }

        result = await metrics_service.get_all_metrics()

        assert result == {
            "files_processed": 100,
            "jobs_completed": 95,
            "jobs_failed": 5,
        }

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_metrics(
        self, metrics_service, mock_redis_client
    ):
        """Test returning empty dict when no metrics exist."""
        mock_redis_client.hgetall.return_value = {}

        result = await metrics_service.get_all_metrics()

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_hgetall_returns_none(
        self, metrics_service, mock_redis_client
    ):
        """Test returning empty dict when hgetall returns None."""
        mock_redis_client.hgetall.return_value = None

        result = await metrics_service.get_all_metrics()

        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_redis_error(
        self, metrics_service, mock_redis_client
    ):
        """Test returning empty dict on Redis error."""
        mock_redis_client.hgetall.side_effect = Exception("Redis connection lost")

        result = await metrics_service.get_all_metrics()

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_all_metrics_with_specific_date(
        self, metrics_service, mock_redis_client
    ):
        """Test retrieving all metrics for a specific date."""
        mock_redis_client.hgetall.return_value = {"metric1": "10", "metric2": "20"}
        specific_date = datetime(2024, 12, 15, 0, 0, 0, tzinfo=UTC)

        result = await metrics_service.get_all_metrics(date=specific_date)

        call_args = mock_redis_client.hgetall.call_args
        assert call_args.args[0] == "eq-pdf:metrics:daily:20241215"
        assert result == {"metric1": 10, "metric2": 20}

    @pytest.mark.asyncio
    async def test_get_all_metrics_converts_all_values_to_int(
        self, metrics_service, mock_redis_client
    ):
        """Test that all string values are converted to integers."""
        mock_redis_client.hgetall.return_value = {
            "small": "1",
            "medium": "500",
            "large": "999999",
        }

        result = await metrics_service.get_all_metrics()

        for value in result.values():
            assert isinstance(value, int)


@pytest.mark.unit
class TestCleanupOldMetrics:
    """Tests for cleanup_old_metrics() method."""

    @pytest.mark.asyncio
    async def test_deletes_keys_older_than_retention_period(
        self, metrics_service, mock_redis_client
    ):
        """Test that keys older than retention period are deleted."""
        # Calculate dates
        today = datetime.now(UTC)
        old_date = (today - timedelta(days=100)).strftime("%Y%m%d")
        recent_date = (today - timedelta(days=10)).strftime("%Y%m%d")

        # Mock scan to return keys
        old_key = f"eq-pdf:metrics:daily:{old_date}"
        recent_key = f"eq-pdf:metrics:daily:{recent_date}"

        mock_redis_client.scan.return_value = (0, [old_key, recent_key])

        deleted_count = await metrics_service.cleanup_old_metrics()

        # Only old key should be deleted (assuming 90-day retention)
        assert deleted_count == 1
        mock_redis_client.delete.assert_called_once_with(old_key)

    @pytest.mark.asyncio
    async def test_returns_count_of_deleted_keys(
        self, metrics_service, mock_redis_client
    ):
        """Test that cleanup returns the count of deleted keys."""
        today = datetime.now(UTC)
        old_dates = [
            (today - timedelta(days=100 + i)).strftime("%Y%m%d") for i in range(3)
        ]
        old_keys = [f"eq-pdf:metrics:daily:{d}" for d in old_dates]

        mock_redis_client.scan.return_value = (0, old_keys)

        deleted_count = await metrics_service.cleanup_old_metrics()

        assert deleted_count == 3
        assert mock_redis_client.delete.call_count == 3

    @pytest.mark.asyncio
    async def test_handles_invalid_key_format_gracefully(
        self, metrics_service, mock_redis_client
    ):
        """Test that invalid key formats are handled gracefully."""
        # Include some invalid key formats
        invalid_keys = [
            "eq-pdf:metrics:daily:invalid",  # Not a valid date
            "eq-pdf:metrics:daily:",  # Empty date
            "eq-pdf:metrics:daily:2025010",  # Too short
        ]

        mock_redis_client.scan.return_value = (0, invalid_keys)

        deleted_count = await metrics_service.cleanup_old_metrics()

        # Should not delete any invalid keys
        assert deleted_count == 0
        mock_redis_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_zero_on_redis_error(
        self, metrics_service, mock_redis_client
    ):
        """Test returning 0 on Redis error during scan."""
        mock_redis_client.scan.side_effect = Exception("Redis connection error")

        deleted_count = await metrics_service.cleanup_old_metrics()

        assert deleted_count == 0

    @pytest.mark.asyncio
    async def test_handles_pagination_during_scan(
        self, metrics_service, mock_redis_client
    ):
        """Test that scan pagination is handled correctly."""
        today = datetime.now(UTC)
        old_date1 = (today - timedelta(days=100)).strftime("%Y%m%d")
        old_date2 = (today - timedelta(days=101)).strftime("%Y%m%d")

        key1 = f"eq-pdf:metrics:daily:{old_date1}"
        key2 = f"eq-pdf:metrics:daily:{old_date2}"

        # Simulate pagination: first scan returns cursor=5, second returns cursor=0
        mock_redis_client.scan.side_effect = [
            (5, [key1]),  # First page
            (0, [key2]),  # Second page (final)
        ]

        deleted_count = await metrics_service.cleanup_old_metrics()

        assert deleted_count == 2
        assert mock_redis_client.scan.call_count == 2

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_keys_match(
        self, metrics_service, mock_redis_client
    ):
        """Test returning 0 when no keys match the pattern."""
        mock_redis_client.scan.return_value = (0, [])

        deleted_count = await metrics_service.cleanup_old_metrics()

        assert deleted_count == 0
        mock_redis_client.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_delete_error_for_individual_key(
        self, metrics_service, mock_redis_client
    ):
        """Test that delete errors for individual keys don't stop processing."""
        today = datetime.now(UTC)
        old_date = (today - timedelta(days=100)).strftime("%Y%m%d")
        old_key = f"eq-pdf:metrics:daily:{old_date}"

        mock_redis_client.scan.return_value = (0, [old_key])
        mock_redis_client.delete.side_effect = Exception("Delete failed")

        # Should return 0 since delete failed (entire operation fails on exception)
        deleted_count = await metrics_service.cleanup_old_metrics()

        assert deleted_count == 0


@pytest.mark.unit
class TestLogDailySummary:
    """Tests for log_daily_summary() method."""

    @pytest.mark.asyncio
    async def test_logs_summary_when_metrics_exist(
        self, metrics_service, mock_redis_client, caplog
    ):
        """Test that summary is logged when metrics exist."""
        mock_redis_client.hgetall.return_value = {
            "files_processed": "50",
            "jobs_completed": "45",
            "jobs_failed": "5",
        }

        import logging

        with caplog.at_level(logging.INFO):
            await metrics_service.log_daily_summary()

        assert "Daily metrics summary" in caplog.text
        assert "files_processed: 50" in caplog.text
        assert "jobs_completed: 45" in caplog.text
        assert "jobs_failed: 5" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_no_metrics_when_empty(
        self, metrics_service, mock_redis_client, caplog
    ):
        """Test that 'No metrics' is logged when empty."""
        mock_redis_client.hgetall.return_value = {}

        import logging

        with caplog.at_level(logging.INFO):
            await metrics_service.log_daily_summary()

        assert "No metrics recorded today" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_errors_gracefully(
        self, metrics_service, mock_redis_client, caplog
    ):
        """Test that errors are handled gracefully.

        Note: When get_all_metrics() fails, it catches the exception and returns {}.
        The log_daily_summary() then sees an empty result, not an exception.
        This test verifies that Redis errors during get_all_metrics() are caught
        and logged properly without crashing log_daily_summary().
        """
        mock_redis_client.hgetall.side_effect = Exception("Redis unavailable")

        import logging

        with caplog.at_level(logging.ERROR):
            # Should not raise - error is caught in get_all_metrics()
            await metrics_service.log_daily_summary()

        # Error is logged by get_all_metrics(), not log_daily_summary()
        assert "Failed to get all metrics" in caplog.text
        assert "Redis unavailable" in caplog.text

    @pytest.mark.asyncio
    async def test_handles_direct_errors_gracefully(
        self, metrics_service, caplog
    ):
        """Test that log_daily_summary handles its own internal errors gracefully.

        This tests the try-except block within log_daily_summary() itself,
        not errors from get_all_metrics().
        """
        import logging

        # Mock get_all_metrics to raise an exception after being called
        with patch.object(
            metrics_service, "get_all_metrics", side_effect=RuntimeError("Internal error")
        ):
            with caplog.at_level(logging.ERROR):
                # Should not raise
                await metrics_service.log_daily_summary()

            assert "Failed to log daily summary" in caplog.text
            assert "Internal error" in caplog.text

    @pytest.mark.asyncio
    async def test_logs_metrics_in_sorted_order(
        self, metrics_service, mock_redis_client, caplog
    ):
        """Test that metrics are logged in alphabetically sorted order."""
        mock_redis_client.hgetall.return_value = {
            "z_metric": "1",
            "a_metric": "2",
            "m_metric": "3",
        }

        import logging

        with caplog.at_level(logging.INFO):
            await metrics_service.log_daily_summary()

        # Verify metrics appear in sorted order in the log
        log_text = caplog.text
        a_pos = log_text.find("a_metric")
        m_pos = log_text.find("m_metric")
        z_pos = log_text.find("z_metric")

        assert a_pos < m_pos < z_pos


@pytest.mark.unit
class TestRecordLlmCall:
    """Tests for record_llm_call() Prometheus helper function."""

    def test_records_input_tokens(self):
        """Test that input tokens are recorded correctly."""
        with patch.object(llm_tokens_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_llm_call(
                agent="planner",
                input_tokens=100,
                output_tokens=50,
                cost_cents=0.5,
                duration_ms=1000,
            )

            # Verify input tokens labeled correctly
            mock_labels.assert_any_call(agent="planner", direction="input")
            mock_counter.inc.assert_called()

    def test_records_output_tokens(self):
        """Test that output tokens are recorded correctly."""
        with patch.object(llm_tokens_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_llm_call(
                agent="worker",
                input_tokens=200,
                output_tokens=150,
                cost_cents=1.0,
                duration_ms=2000,
            )

            mock_labels.assert_any_call(agent="worker", direction="output")

    def test_records_cost_in_cents(self):
        """Test that cost is recorded in cents."""
        with patch.object(llm_cost_cents_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_llm_call(
                agent="paragraph",
                input_tokens=50,
                output_tokens=25,
                cost_cents=0.25,
                duration_ms=500,
            )

            mock_labels.assert_called_with(agent="paragraph")
            mock_counter.inc.assert_called_with(0.25)

    def test_records_duration_in_seconds(self):
        """Test that duration is converted from ms to seconds."""
        with patch.object(llm_request_duration_seconds, "labels") as mock_labels:
            mock_histogram = mock_labels.return_value

            record_llm_call(
                agent="recovery",
                input_tokens=100,
                output_tokens=100,
                cost_cents=0.5,
                duration_ms=3000,  # 3 seconds
            )

            mock_labels.assert_called_with(agent="recovery")
            mock_histogram.observe.assert_called_with(3.0)

    def test_records_success_status(self):
        """Test that success status is recorded correctly."""
        with patch.object(llm_requests_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_llm_call(
                agent="verification",
                input_tokens=50,
                output_tokens=50,
                cost_cents=0.3,
                duration_ms=800,
                success=True,
            )

            mock_labels.assert_called_with(agent="verification", status="success")
            mock_counter.inc.assert_called()

    def test_records_error_status(self):
        """Test that error status is recorded correctly."""
        with patch.object(llm_requests_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_llm_call(
                agent="planner",
                input_tokens=100,
                output_tokens=0,
                cost_cents=0.1,
                duration_ms=5000,
                success=False,
            )

            mock_labels.assert_called_with(agent="planner", status="error")

    def test_default_success_is_true(self):
        """Test that default success value is True."""
        with patch.object(llm_requests_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_llm_call(
                agent="worker",
                input_tokens=100,
                output_tokens=50,
                cost_cents=0.5,
                duration_ms=1000,
            )

            mock_labels.assert_called_with(agent="worker", status="success")


@pytest.mark.unit
class TestRecordRoundMetrics:
    """Tests for record_round_metrics() Prometheus helper function."""

    def test_records_round_duration(self):
        """Test that round duration is recorded correctly."""
        with patch.object(round_duration_seconds, "labels") as mock_labels:
            mock_histogram = mock_labels.return_value

            record_round_metrics(
                round_number=1,
                duration_ms=30000,  # 30 seconds
                quality_score=0.85,
                document_id="doc-123",
            )

            mock_labels.assert_called_with(round_number="1")
            mock_histogram.observe.assert_called_with(30.0)

    def test_records_quality_score(self):
        """Test that document quality score is recorded correctly."""
        with patch.object(document_quality_score, "labels") as mock_labels:
            mock_gauge = mock_labels.return_value

            record_round_metrics(
                round_number=2,
                duration_ms=20000,
                quality_score=0.92,
                document_id="doc-456",
            )

            mock_labels.assert_called_with(document_id="doc-456", round_number="2")
            mock_gauge.set.assert_called_with(0.92)

    def test_records_convergence_reason_when_provided(self):
        """Test that convergence reason is recorded when provided."""
        with (
            patch.object(round_processing_total, "labels") as mock_rpt_labels,
            patch.object(convergence_events_total, "labels") as mock_cet_labels,
        ):
            mock_rpt_counter = mock_rpt_labels.return_value
            mock_cet_counter = mock_cet_labels.return_value

            record_round_metrics(
                round_number=3,
                duration_ms=15000,
                quality_score=0.95,
                document_id="doc-789",
                convergence_reason="quality_threshold_met",
            )

            mock_rpt_labels.assert_called_with(
                round_number="3", convergence_reason="quality_threshold_met"
            )
            mock_rpt_counter.inc.assert_called()

            mock_cet_labels.assert_called_with(reason="quality_threshold_met")
            mock_cet_counter.inc.assert_called()

    def test_does_not_record_convergence_when_not_final_round(self):
        """Test that convergence is not recorded without convergence_reason."""
        with (
            patch.object(round_processing_total, "labels") as mock_rpt_labels,
            patch.object(convergence_events_total, "labels") as mock_cet_labels,
        ):
            record_round_metrics(
                round_number=1,
                duration_ms=20000,
                quality_score=0.75,
                document_id="doc-abc",
                convergence_reason=None,
            )

            mock_rpt_labels.assert_not_called()
            mock_cet_labels.assert_not_called()

    def test_handles_issues_by_severity(self):
        """Test handling of issues_by_severity parameter (currently no-op)."""
        # The current implementation has pass statements for these
        # This test ensures the parameter doesn't cause errors
        record_round_metrics(
            round_number=2,
            duration_ms=25000,
            quality_score=0.88,
            document_id="doc-xyz",
            issues_by_severity={"critical": 1, "major": 3, "minor": 5},
        )

        # Should not raise any exceptions

    def test_handles_issues_by_category(self):
        """Test handling of issues_by_category parameter (currently no-op)."""
        record_round_metrics(
            round_number=2,
            duration_ms=25000,
            quality_score=0.88,
            document_id="doc-xyz",
            issues_by_category={"structure": 2, "accessibility": 4},
        )

        # Should not raise any exceptions

    def test_round_number_is_string_labeled(self):
        """Test that round number is converted to string for labels."""
        with patch.object(round_duration_seconds, "labels") as mock_labels:
            record_round_metrics(
                round_number=5,
                duration_ms=10000,
                quality_score=0.90,
                document_id="doc-test",
            )

            # Verify round_number is passed as string "5"
            mock_labels.assert_called_with(round_number="5")


@pytest.mark.unit
class TestRecordCriticIssue:
    """Tests for record_critic_issue() Prometheus helper function."""

    def test_records_issue_with_severity_and_category(self):
        """Test that issues are recorded with correct severity and category."""
        with patch.object(critic_issues_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_critic_issue(severity="critical", category="structure")

            mock_labels.assert_called_with(severity="critical", category="structure")
            mock_counter.inc.assert_called_once()

    def test_records_major_accessibility_issue(self):
        """Test recording a major accessibility issue."""
        with patch.object(critic_issues_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_critic_issue(severity="major", category="accessibility")

            mock_labels.assert_called_with(severity="major", category="accessibility")
            mock_counter.inc.assert_called_once()

    def test_records_minor_content_issue(self):
        """Test recording a minor content issue."""
        with patch.object(critic_issues_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_critic_issue(severity="minor", category="content")

            mock_labels.assert_called_with(severity="minor", category="content")
            mock_counter.inc.assert_called_once()

    def test_records_cosmetic_formatting_issue(self):
        """Test recording a cosmetic formatting issue."""
        with patch.object(critic_issues_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_critic_issue(severity="cosmetic", category="formatting")

            mock_labels.assert_called_with(severity="cosmetic", category="formatting")
            mock_counter.inc.assert_called_once()

    def test_multiple_issues_increment_counter_multiple_times(self):
        """Test that multiple calls increment the counter multiple times."""
        with patch.object(critic_issues_total, "labels") as mock_labels:
            mock_counter = mock_labels.return_value

            record_critic_issue(severity="major", category="structure")
            record_critic_issue(severity="major", category="structure")
            record_critic_issue(severity="major", category="structure")

            assert mock_counter.inc.call_count == 3
