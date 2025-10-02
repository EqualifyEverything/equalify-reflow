"""Tests for retry logic and error categorization.

Tests the retry_helpers module to ensure:
1. Retryable errors trigger retries with exponential backoff
2. Non-retryable errors fail immediately without retries
3. Max retry attempts are respected
4. Exponential backoff timing is correct
5. Successful retries after transient failures
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock
from botocore.exceptions import ClientError, BotoCoreError
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError

from src.utils.retry_helpers import (
    is_retryable_error,
    retry_with_backoff,
    retry_with_backoff_sync,
    RETRYABLE_BOTO_ERROR_CODES,
    NON_RETRYABLE_BOTO_ERROR_CODES,
    RETRYABLE_HTTP_CODES
)


class TestErrorCategorization:
    """Tests for is_retryable_error() function."""

    def test_retryable_boto_error_codes(self):
        """Test that known retryable boto error codes are identified."""
        for error_code in RETRYABLE_BOTO_ERROR_CODES:
            error = ClientError(
                {'Error': {'Code': error_code}},
                'TestOperation'
            )
            assert is_retryable_error(error), f"Expected {error_code} to be retryable"

    def test_non_retryable_boto_error_codes(self):
        """Test that known non-retryable boto error codes are identified."""
        for error_code in NON_RETRYABLE_BOTO_ERROR_CODES:
            error = ClientError(
                {'Error': {'Code': error_code}},
                'TestOperation'
            )
            assert not is_retryable_error(error), f"Expected {error_code} to be non-retryable"

    def test_retryable_http_status_codes(self):
        """Test that retryable HTTP status codes are identified."""
        for http_code in RETRYABLE_HTTP_CODES:
            error = ClientError(
                {
                    'Error': {'Code': 'UnknownError'},
                    'ResponseMetadata': {'HTTPStatusCode': http_code}
                },
                'TestOperation'
            )
            assert is_retryable_error(error), f"Expected HTTP {http_code} to be retryable"

    def test_non_retryable_http_status_codes(self):
        """Test that non-retryable HTTP status codes are identified."""
        non_retryable_codes = [400, 403, 404]
        for http_code in non_retryable_codes:
            error = ClientError(
                {
                    'Error': {'Code': 'UnknownError'},
                    'ResponseMetadata': {'HTTPStatusCode': http_code}
                },
                'TestOperation'
            )
            assert not is_retryable_error(error), f"Expected HTTP {http_code} to be non-retryable"

    def test_botocore_errors_retryable(self):
        """Test that BotoCoreError instances are retryable."""
        error = BotoCoreError()
        assert is_retryable_error(error)

    def test_network_errors_retryable(self):
        """Test that network errors are retryable."""
        assert is_retryable_error(asyncio.TimeoutError())
        assert is_retryable_error(ConnectionError("Connection refused"))
        assert is_retryable_error(TimeoutError("Request timeout"))

    def test_redis_errors_retryable(self):
        """Test that Redis errors are retryable."""
        assert is_retryable_error(RedisError("Redis error"))
        assert is_retryable_error(RedisConnectionError("Connection lost"))

    def test_unknown_errors_non_retryable(self):
        """Test that unknown errors default to non-retryable."""
        assert not is_retryable_error(ValueError("Invalid value"))
        assert not is_retryable_error(KeyError("Missing key"))
        assert not is_retryable_error(Exception("Generic error"))


class TestRetryWithBackoff:
    """Tests for retry_with_backoff() async function."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        """Test successful execution on first attempt (no retries)."""
        mock_func = AsyncMock(return_value="success")

        result = await retry_with_backoff(
            mock_func,
            max_attempts=3,
            operation_name="test_operation"
        )

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_success_after_retryable_errors(self):
        """Test successful execution after transient errors."""
        # Fail twice with retryable errors, then succeed
        mock_func = AsyncMock(side_effect=[
            ClientError({'Error': {'Code': 'RequestTimeout'}}, 'op'),
            ClientError({'Error': {'Code': 'ServiceUnavailable'}}, 'op'),
            "success"
        ])

        result = await retry_with_backoff(
            mock_func,
            max_attempts=3,
            base_delay=0.1,
            operation_name="test_operation"
        )

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_immediately(self):
        """Test that non-retryable errors fail without retries."""
        non_retryable_error = ClientError(
            {'Error': {'Code': 'NoSuchKey'}},
            'GetObject'
        )
        mock_func = AsyncMock(side_effect=non_retryable_error)

        with pytest.raises(ClientError) as exc_info:
            await retry_with_backoff(
                mock_func,
                max_attempts=3,
                operation_name="test_operation"
            )

        assert exc_info.value.response['Error']['Code'] == 'NoSuchKey'
        assert mock_func.call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_max_retries_respected(self):
        """Test that max_attempts is respected for retryable errors."""
        retryable_error = ClientError(
            {'Error': {'Code': 'RequestTimeout'}},
            'op'
        )
        mock_func = AsyncMock(side_effect=retryable_error)

        with pytest.raises(ClientError):
            await retry_with_backoff(
                mock_func,
                max_attempts=3,
                base_delay=0.1,
                operation_name="test_operation"
            )

        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Test that exponential backoff delays are correct."""
        import time

        retryable_error = asyncio.TimeoutError()
        mock_func = AsyncMock(side_effect=retryable_error)

        start_time = time.time()

        with pytest.raises(asyncio.TimeoutError):
            await retry_with_backoff(
                mock_func,
                max_attempts=3,
                base_delay=0.1,
                backoff_factor=2.0,
                operation_name="test_operation"
            )

        elapsed_time = time.time() - start_time

        # Expected delays: 0.1s (after 1st fail) + 0.2s (after 2nd fail) = 0.3s total
        # Add buffer for execution time
        assert elapsed_time >= 0.3
        assert elapsed_time < 0.5  # Should not exceed expected delays significantly

    @pytest.mark.asyncio
    async def test_max_delay_cap(self):
        """Test that max_delay caps the exponential backoff."""
        retryable_error = ConnectionError()
        mock_func = AsyncMock(side_effect=retryable_error)

        start_time = asyncio.get_event_loop().time()

        with pytest.raises(ConnectionError):
            await retry_with_backoff(
                mock_func,
                max_attempts=3,
                base_delay=10.0,
                max_delay=0.2,  # Cap at 0.2s
                backoff_factor=2.0,
                operation_name="test_operation"
            )

        elapsed_time = asyncio.get_event_loop().time() - start_time

        # All delays should be capped at max_delay (0.2s)
        # 2 retries × 0.2s = 0.4s total
        assert elapsed_time >= 0.4
        assert elapsed_time < 0.6

    @pytest.mark.asyncio
    async def test_with_lambda_wrapper(self):
        """Test retry with lambda wrapper for functions with arguments."""
        async def fetch_data(key: str) -> str:
            if key == "fail":
                raise ClientError({'Error': {'Code': 'RequestTimeout'}}, 'op')
            return f"data_{key}"

        # Should fail and retry
        with pytest.raises(ClientError):
            await retry_with_backoff(
                lambda: fetch_data("fail"),
                max_attempts=2,
                base_delay=0.1,
                operation_name="fetch_data"
            )

        # Should succeed on first attempt
        result = await retry_with_backoff(
            lambda: fetch_data("success"),
            max_attempts=3,
            operation_name="fetch_data"
        )
        assert result == "data_success"


class TestRetryWithBackoffSync:
    """Tests for retry_with_backoff_sync() synchronous function."""

    @pytest.mark.asyncio
    async def test_sync_function_success(self):
        """Test synchronous function execution with retries."""
        call_count = 0

        def sync_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Network error")
            return "success"

        result = await retry_with_backoff_sync(
            sync_func,
            max_attempts=3,
            base_delay=0.1,
            operation_name="sync_operation"
        )

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_sync_non_retryable_error(self):
        """Test that sync function fails immediately on non-retryable error."""
        def sync_func():
            raise ClientError(
                {'Error': {'Code': 'AccessDenied'}},
                'op'
            )

        with pytest.raises(ClientError) as exc_info:
            await retry_with_backoff_sync(
                sync_func,
                max_attempts=3,
                operation_name="sync_operation"
            )

        assert exc_info.value.response['Error']['Code'] == 'AccessDenied'


class TestIntegrationScenarios:
    """Integration tests for realistic retry scenarios."""

    @pytest.mark.asyncio
    async def test_s3_download_with_transient_failures(self):
        """Simulate S3 download with transient network failures."""
        mock_s3_client = Mock()

        # Simulate: timeout, throttle, then success
        mock_s3_client.get_object.side_effect = [
            ClientError({'Error': {'Code': 'RequestTimeout'}}, 'GetObject'),
            ClientError(
                {
                    'Error': {'Code': 'ThrottlingException'},
                    'ResponseMetadata': {'HTTPStatusCode': 429}
                },
                'GetObject'
            ),
            {'Body': Mock(read=lambda: b"PDF content")}
        ]

        async def download_file(key: str):
            response = mock_s3_client.get_object(Bucket='bucket', Key=key)
            return response['Body'].read()

        result = await retry_with_backoff(
            lambda: download_file("test.pdf"),
            max_attempts=3,
            base_delay=0.1,
            operation_name="S3 download"
        )

        assert result == b"PDF content"
        assert mock_s3_client.get_object.call_count == 3

    @pytest.mark.asyncio
    async def test_redis_operation_with_connection_error(self):
        """Simulate Redis operation with connection errors."""
        mock_redis = AsyncMock()

        # Simulate connection error then success
        mock_redis.lpush.side_effect = [
            RedisConnectionError("Connection refused"),
            RedisConnectionError("Connection lost"),
            1  # Success (returns number of items)
        ]

        result = await retry_with_backoff(
            lambda: mock_redis.lpush("queue", "data"),
            max_attempts=3,
            base_delay=0.1,
            operation_name="Redis enqueue"
        )

        assert result == 1
        assert mock_redis.lpush.call_count == 3

    @pytest.mark.asyncio
    async def test_permanent_s3_error_no_retry(self):
        """Test that permanent S3 errors (NoSuchKey) fail immediately."""
        mock_s3_client = Mock()
        mock_s3_client.get_object.side_effect = ClientError(
            {'Error': {'Code': 'NoSuchKey'}},
            'GetObject'
        )

        async def download_file(key: str):
            return mock_s3_client.get_object(Bucket='bucket', Key=key)

        with pytest.raises(ClientError) as exc_info:
            await retry_with_backoff(
                lambda: download_file("missing.pdf"),
                max_attempts=3,
                operation_name="S3 download"
            )

        assert exc_info.value.response['Error']['Code'] == 'NoSuchKey'
        assert mock_s3_client.get_object.call_count == 1  # No retries
