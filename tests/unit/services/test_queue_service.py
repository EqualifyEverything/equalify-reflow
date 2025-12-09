"""Integration tests for Queue Service."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from src.services.queue_service import QueueService
from src.shared.models.queue import ProcessingQueuePayload

from tests.conftest_fixtures.data_factories import create_pii_queue_payload


class SamplePayload(BaseModel):
    """Simple test payload for testing."""

    job_id: str
    data: str


@pytest.fixture
def queue_service(mock_redis_client):
    """Create queue service with mock client."""
    return QueueService(redis_client=mock_redis_client)


@pytest.fixture
def sample_pii_payload():
    """Create sample PII queue payload using factory."""
    return create_pii_queue_payload(
        job_id="550e8400-e29b-41d4-a716-446655440000",
        s3_key="temp/550e8400-e29b-41d4-a716-446655440000/document.pdf",
        created_at=datetime(2024, 1, 15, 10, 30, 0)
    )


class TestQueuePiiJob:
    """Tests for queue_pii_job method (legacy method)."""

    @pytest.mark.asyncio
    async def test_queue_pii_job_success(self, queue_service, mock_redis_client):
        """Test queuing a PII job."""
        mock_redis_client.lpush.return_value = 1

        await queue_service.queue_pii_job(
            job_id="job123",
            s3_key="temp/job123/file.pdf"
        )

        # Verify lpush was called
        mock_redis_client.lpush.assert_called_once()
        queue_name, payload_json = mock_redis_client.lpush.call_args.args

        # Verify payload structure
        payload = json.loads(payload_json)
        assert payload["job_id"] == "job123"
        assert payload["s3_key"] == "temp/job123/file.pdf"
        assert "created_at" in payload


class TestEnqueue:
    """Tests for generic enqueue method."""

    @pytest.mark.asyncio
    async def test_enqueue_pydantic_model(self, queue_service, mock_redis_client, sample_pii_payload):
        """Test enqueuing a Pydantic model."""
        mock_redis_client.lpush.return_value = 1

        await queue_service.enqueue("test_queue", sample_pii_payload)

        # Verify lpush was called with correct data
        mock_redis_client.lpush.assert_called_once()
        queue_name, payload_json = mock_redis_client.lpush.call_args.args
        assert queue_name == "test_queue"

        # Verify JSON serialization
        payload = json.loads(payload_json)
        assert payload["job_id"] == sample_pii_payload.job_id
        assert payload["s3_key"] == sample_pii_payload.s3_key

    @pytest.mark.asyncio
    async def test_enqueue_processing_payload(self, queue_service, mock_redis_client):
        """Test enqueuing processing queue payload."""
        payload = ProcessingQueuePayload(
            job_id="550e8400-e29b-41d4-a716-446655440000",
            s3_key="temp/file.pdf",
            approved_at=None
        )
        mock_redis_client.lpush.return_value = 1

        await queue_service.enqueue("processing_queue", payload)

        mock_redis_client.lpush.assert_called_once()
        queue_name, _ = mock_redis_client.lpush.call_args.args
        assert queue_name == "processing_queue"

    @pytest.mark.asyncio
    async def test_enqueue_failure(self, queue_service, mock_redis_client, sample_pii_payload):
        """Test handling of enqueue failure."""
        mock_redis_client.lpush.side_effect = Exception("Redis connection error")

        with pytest.raises(Exception) as exc:
            await queue_service.enqueue("test_queue", sample_pii_payload)

        assert "Failed to enqueue" in str(exc.value)


class TestDequeue:
    """Tests for dequeue method."""

    @pytest.mark.asyncio
    async def test_dequeue_success(self, queue_service, mock_redis_client):
        """Test successful dequeue operation."""
        payload_dict = {"job_id": "job123", "data": "test"}
        payload_json = json.dumps(payload_dict)
        mock_redis_client.brpop.return_value = ("test_queue", payload_json)

        result = await queue_service.dequeue("test_queue", timeout=5)

        assert result == payload_dict
        mock_redis_client.brpop.assert_called_once_with("test_queue", timeout=5)

    @pytest.mark.asyncio
    async def test_dequeue_with_model_validation(self, queue_service, mock_redis_client):
        """Test dequeue with Pydantic model validation."""
        payload_dict = {"job_id": "job123", "data": "test"}
        payload_json = json.dumps(payload_dict)
        mock_redis_client.brpop.return_value = ("test_queue", payload_json)

        result = await queue_service.dequeue(
            "test_queue",
            timeout=5,
            model_class=SamplePayload
        )

        assert result["job_id"] == "job123"
        assert result["data"] == "test"

    @pytest.mark.asyncio
    async def test_dequeue_timeout(self, queue_service, mock_redis_client):
        """Test dequeue timeout (no items in queue)."""
        mock_redis_client.brpop.return_value = None

        result = await queue_service.dequeue("empty_queue", timeout=1)

        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_invalid_json(self, queue_service, mock_redis_client):
        """Test handling of invalid JSON in queue."""
        mock_redis_client.brpop.return_value = ("test_queue", "invalid json{")

        with pytest.raises(Exception) as exc:
            await queue_service.dequeue("test_queue")

        assert "Failed to deserialize" in str(exc.value)

    @pytest.mark.asyncio
    async def test_dequeue_connection_error(self, queue_service, mock_redis_client):
        """Test handling of Redis connection error."""
        mock_redis_client.brpop.side_effect = Exception("Connection lost")

        with pytest.raises(Exception) as exc:
            await queue_service.dequeue("test_queue")

        assert "Failed to dequeue" in str(exc.value)


class TestQueueDepth:
    """Tests for queue_depth method."""

    @pytest.mark.asyncio
    async def test_queue_depth_empty(self, queue_service, mock_redis_client):
        """Test depth of empty queue."""
        mock_redis_client.llen.return_value = 0

        depth = await queue_service.queue_depth("empty_queue")

        assert depth == 0
        mock_redis_client.llen.assert_called_once_with("empty_queue")

    @pytest.mark.asyncio
    async def test_queue_depth_with_items(self, queue_service, mock_redis_client):
        """Test depth of queue with items."""
        mock_redis_client.llen.return_value = 5

        depth = await queue_service.queue_depth("active_queue")

        assert depth == 5

    @pytest.mark.asyncio
    async def test_queue_depth_error(self, queue_service, mock_redis_client):
        """Test handling of depth check error."""
        mock_redis_client.llen.side_effect = Exception("Redis error")

        depth = await queue_service.queue_depth("error_queue")

        assert depth == -1  # Error indicator


class TestCheckQueueDepth:
    """Tests for check_queue_depth method (legacy)."""

    @pytest.mark.asyncio
    async def test_check_queue_depth_success(self, queue_service, mock_redis_client):
        """Test checking queue depth."""
        mock_redis_client.llen.return_value = 3

        depth = await queue_service.check_queue_depth()

        assert depth == 3


class TestPeekQueue:
    """Tests for peek_queue method."""

    @pytest.mark.asyncio
    async def test_peek_queue_success(self, queue_service, mock_redis_client):
        """Test peeking at queue items."""
        items = [
            json.dumps({"job_id": "job1", "data": "test1"}),
            json.dumps({"job_id": "job2", "data": "test2"}),
        ]
        mock_redis_client.lrange.return_value = items

        result = await queue_service.peek_queue("test_queue", count=10)

        assert len(result) == 2
        assert result[0]["job_id"] == "job1"
        assert result[1]["job_id"] == "job2"
        mock_redis_client.lrange.assert_called_once_with("test_queue", 0, 9)

    @pytest.mark.asyncio
    async def test_peek_queue_custom_count(self, queue_service, mock_redis_client):
        """Test peeking with custom count."""
        mock_redis_client.lrange.return_value = []

        await queue_service.peek_queue("test_queue", count=5)

        mock_redis_client.lrange.assert_called_once_with("test_queue", 0, 4)

    @pytest.mark.asyncio
    async def test_peek_queue_empty(self, queue_service, mock_redis_client):
        """Test peeking at empty queue."""
        mock_redis_client.lrange.return_value = []

        result = await queue_service.peek_queue("empty_queue")

        assert result == []

    @pytest.mark.asyncio
    async def test_peek_queue_skip_invalid_json(self, queue_service, mock_redis_client):
        """Test that invalid JSON items are skipped."""
        items = [
            json.dumps({"job_id": "job1"}),
            "invalid json{",
            json.dumps({"job_id": "job2"}),
        ]
        mock_redis_client.lrange.return_value = items

        result = await queue_service.peek_queue("test_queue")

        assert len(result) == 2  # Invalid item skipped
        assert result[0]["job_id"] == "job1"
        assert result[1]["job_id"] == "job2"

    @pytest.mark.asyncio
    async def test_peek_queue_error(self, queue_service, mock_redis_client):
        """Test handling of peek error."""
        mock_redis_client.lrange.side_effect = Exception("Redis error")

        with pytest.raises(Exception) as exc:
            await queue_service.peek_queue("test_queue")

        assert "Failed to peek queue" in str(exc.value)


class TestHealthCheck:
    """Tests for health check methods."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, queue_service, mock_redis_client):
        """Test health check when Redis is healthy."""
        mock_redis_client.ping.return_value = True

        healthy = await queue_service.health_check()

        assert healthy is True
        mock_redis_client.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self, queue_service, mock_redis_client):
        """Test health check when Redis is down."""
        mock_redis_client.ping.side_effect = Exception("Connection refused")

        healthy = await queue_service.health_check()

        assert healthy is False

    @pytest.mark.asyncio
    async def test_check_redis_connection(self, queue_service, mock_redis_client):
        """Test Redis connection check."""
        mock_redis_client.ping.return_value = True

        connected = await queue_service.check_redis_connection()

        assert connected is True


class TestTimeoutTracking:
    """Tests for timeout tracking sorted set operations."""

    @pytest.mark.asyncio
    async def test_add_to_timeout_tracking(self, queue_service, mock_redis_client):
        """Test adding job to timeout tracking."""
        from datetime import datetime, timedelta

        expires_at = datetime.now(UTC) + timedelta(hours=4)

        await queue_service.add_to_timeout_tracking("job123", expires_at)

        # Verify ZADD was called with correct parameters
        mock_redis_client.zadd.assert_called_once()
        call_args = mock_redis_client.zadd.call_args
        assert call_args[0][0] == "eq-pdf:timeouts:approval"
        assert "job123" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_add_to_timeout_tracking_custom_type(self, queue_service, mock_redis_client):
        """Test adding with custom timeout type."""
        from datetime import datetime

        expires_at = datetime.now(UTC)

        await queue_service.add_to_timeout_tracking(
            "job456",
            expires_at,
            timeout_type="processing"
        )

        call_args = mock_redis_client.zadd.call_args
        assert call_args[0][0] == "eq-pdf:timeouts:processing"

    @pytest.mark.asyncio
    async def test_add_to_timeout_tracking_error(self, queue_service, mock_redis_client):
        """Test error handling when adding to timeout tracking."""
        from datetime import datetime

        mock_redis_client.zadd.side_effect = Exception("Redis error")
        expires_at = datetime.now(UTC)

        with pytest.raises(Exception) as exc:
            await queue_service.add_to_timeout_tracking("job789", expires_at)

        assert "Failed to add timeout tracking" in str(exc.value)

    @pytest.mark.asyncio
    async def test_get_expired_timeouts(self, queue_service, mock_redis_client):
        """Test getting expired timeouts from sorted set."""
        # Mock Redis response: [(job_id, timestamp), ...]
        mock_redis_client.zrangebyscore.return_value = [
            (b"job123", 1609459200.0),  # Expired job 1
            (b"job456", 1609462800.0)   # Expired job 2
        ]

        expired = await queue_service.get_expired_timeouts()

        assert len(expired) == 2
        assert expired[0] == ("job123", 1609459200.0)
        assert expired[1] == ("job456", 1609462800.0)

        # Verify ZRANGEBYSCORE was called
        mock_redis_client.zrangebyscore.assert_called_once()
        call_kwargs = mock_redis_client.zrangebyscore.call_args.kwargs
        assert call_kwargs["min"] == 0
        assert call_kwargs["withscores"] is True

    @pytest.mark.asyncio
    async def test_get_expired_timeouts_none(self, queue_service, mock_redis_client):
        """Test when no timeouts have expired."""
        mock_redis_client.zrangebyscore.return_value = []

        expired = await queue_service.get_expired_timeouts()

        assert len(expired) == 0

    @pytest.mark.asyncio
    async def test_get_expired_timeouts_handles_strings(self, queue_service, mock_redis_client):
        """Test handling of string job IDs (not bytes)."""
        mock_redis_client.zrangebyscore.return_value = [
            ("job789", 1609459200.0)  # String instead of bytes
        ]

        expired = await queue_service.get_expired_timeouts()

        assert len(expired) == 1
        assert expired[0] == ("job789", 1609459200.0)

    @pytest.mark.asyncio
    async def test_get_expired_timeouts_error_returns_empty(self, queue_service, mock_redis_client):
        """Test fail-safe behavior on error."""
        mock_redis_client.zrangebyscore.side_effect = Exception("Redis error")

        # Should return empty list instead of raising
        expired = await queue_service.get_expired_timeouts()

        assert expired == []

    @pytest.mark.asyncio
    async def test_get_expired_timeouts_rejects_non_string_type(self, queue_service):
        """Test that passing non-string timeout_type raises TypeError.

        This prevents the common mistake of passing a timestamp float
        instead of a timeout type string like 'approval'.
        """
        with pytest.raises(TypeError) as exc:
            await queue_service.get_expired_timeouts(1609459200.0)

        assert "timeout_type must be a string" in str(exc.value)
        assert "Do not pass a timestamp" in str(exc.value)

    @pytest.mark.asyncio
    async def test_remove_from_timeout_tracking(self, queue_service, mock_redis_client):
        """Test removing job from timeout tracking."""
        mock_redis_client.zrem.return_value = 1  # 1 member removed

        removed = await queue_service.remove_from_timeout_tracking("job123")

        assert removed is True
        mock_redis_client.zrem.assert_called_once_with(
            "eq-pdf:timeouts:approval",
            "job123"
        )

    @pytest.mark.asyncio
    async def test_remove_from_timeout_tracking_not_present(self, queue_service, mock_redis_client):
        """Test removing job that wasn't in tracking."""
        mock_redis_client.zrem.return_value = 0  # 0 members removed

        removed = await queue_service.remove_from_timeout_tracking("job456")

        assert removed is False

    @pytest.mark.asyncio
    async def test_remove_from_timeout_tracking_custom_type(self, queue_service, mock_redis_client):
        """Test removing with custom timeout type."""
        mock_redis_client.zrem.return_value = 1

        await queue_service.remove_from_timeout_tracking(
            "job789",
            timeout_type="processing"
        )

        mock_redis_client.zrem.assert_called_once_with(
            "eq-pdf:timeouts:processing",
            "job789"
        )

    @pytest.mark.asyncio
    async def test_remove_from_timeout_tracking_error_returns_false(self, queue_service, mock_redis_client):
        """Test fail-safe behavior on error."""
        mock_redis_client.zrem.side_effect = Exception("Redis error")

        # Should return False instead of raising
        removed = await queue_service.remove_from_timeout_tracking("job-error")

        assert removed is False

    @pytest.mark.asyncio
    async def test_get_timeout_count(self, queue_service, mock_redis_client):
        """Test getting count of jobs in timeout tracking."""
        mock_redis_client.zcard.return_value = 5

        count = await queue_service.get_timeout_count()

        assert count == 5
        mock_redis_client.zcard.assert_called_once_with("eq-pdf:timeouts:approval")

    @pytest.mark.asyncio
    async def test_get_timeout_count_empty(self, queue_service, mock_redis_client):
        """Test count when no jobs in tracking."""
        mock_redis_client.zcard.return_value = 0

        count = await queue_service.get_timeout_count()

        assert count == 0

    @pytest.mark.asyncio
    async def test_get_timeout_count_error_returns_negative(self, queue_service, mock_redis_client):
        """Test error handling returns -1."""
        mock_redis_client.zcard.side_effect = Exception("Redis error")

        count = await queue_service.get_timeout_count()

        assert count == -1
