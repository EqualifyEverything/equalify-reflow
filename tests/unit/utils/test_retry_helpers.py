"""Unit tests for retry logic and error categorization.

Tests focus on critical behavior:
1. Correct error categorization (retryable vs non-retryable)
   - Wrong categorization causes infinite retries OR premature failures
   - Every S3 and Redis operation uses this code
"""

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from src.utils.retry_helpers import is_retryable_error


def create_client_error(code: str, http_code: int | None = None) -> ClientError:
    """Helper to create boto3 ClientError with specific error code."""
    response: dict[str, dict[str, str | int]] = {"Error": {"Code": code}}
    if http_code:
        response["ResponseMetadata"] = {"HTTPStatusCode": http_code}
    return ClientError(response, "test_operation")  # type: ignore[arg-type]


class TestIsRetryableError:
    """Tests for is_retryable_error function."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error,should_retry,description",
        [
            # === Non-retryable Boto3 errors (permanent failures) ===
            (
                create_client_error("NoSuchKey"),
                False,
                "NoSuchKey is permanent - file doesn't exist",
            ),
            (
                create_client_error("NoSuchBucket"),
                False,
                "NoSuchBucket is permanent - bucket doesn't exist",
            ),
            (
                create_client_error("AccessDenied"),
                False,
                "AccessDenied is permanent - no permission",
            ),
            (
                create_client_error("InvalidRequest"),
                False,
                "InvalidRequest is permanent - bad request format",
            ),
            (
                create_client_error("InvalidArgument"),
                False,
                "InvalidArgument is permanent - bad parameters",
            ),
            # === Retryable Boto3 errors (transient failures) ===
            (
                create_client_error("RequestTimeout"),
                True,
                "RequestTimeout is transient - try again",
            ),
            (
                create_client_error("ServiceUnavailable"),
                True,
                "ServiceUnavailable is transient - AWS issue",
            ),
            (
                create_client_error("ThrottlingException"),
                True,
                "ThrottlingException is transient - slow down",
            ),
            (
                create_client_error("InternalError"),
                True,
                "InternalError is transient - AWS internal issue",
            ),
            (
                create_client_error("SlowDown"),
                True,
                "SlowDown is transient - S3 rate limiting",
            ),
            # === HTTP status code based retries ===
            (
                create_client_error("Unknown", http_code=503),
                True,
                "HTTP 503 is retryable regardless of error code",
            ),
            (
                create_client_error("Unknown", http_code=500),
                True,
                "HTTP 500 is retryable - server error",
            ),
            (
                create_client_error("Unknown", http_code=429),
                True,
                "HTTP 429 is retryable - rate limited",
            ),
            (
                create_client_error("Unknown", http_code=400),
                False,
                "HTTP 400 is not retryable - bad request",
            ),
        ],
    )
    def test_boto3_error_categorization(
        self, error: ClientError, should_retry: bool, description: str
    ) -> None:
        """Verify Boto3 errors are correctly categorized.

        Catches: Infinite retries on permanent failures OR premature failures on transient errors.
        """
        assert is_retryable_error(error) == should_retry, description

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error,should_retry,description",
        [
            # === Network/timeout errors (always retryable) ===
            (
                TimeoutError(),
                True,
                "TimeoutError is transient - network issue",
            ),
            (
                ConnectionError("Connection refused"),
                True,
                "ConnectionError is transient - network issue",
            ),
            (
                TimeoutError("Timed out"),
                True,
                "TimeoutError is transient - can retry",
            ),
            # === Redis errors (always retryable) ===
            (
                RedisConnectionError("Connection refused"),
                True,
                "Redis connection error is transient",
            ),
            (
                RedisError("Redis error"),
                True,
                "Generic Redis error is transient",
            ),
        ],
    )
    def test_network_and_redis_error_categorization(
        self, error: Exception, should_retry: bool, description: str
    ) -> None:
        """Verify network and Redis errors are correctly categorized.

        Catches: Premature failures on transient network/Redis issues.
        """
        assert is_retryable_error(error) == should_retry, description

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "status_code,should_retry,description",
        [
            (500, True, "500 Internal Server Error is retryable"),
            (502, True, "502 Bad Gateway is retryable"),
            (503, True, "503 Service Unavailable is retryable"),
            (504, True, "504 Gateway Timeout is retryable"),
            (429, True, "429 Too Many Requests is retryable"),
            (408, True, "408 Request Timeout is retryable"),
            (400, False, "400 Bad Request is NOT retryable"),
            (401, False, "401 Unauthorized is NOT retryable"),
            (403, False, "403 Forbidden is NOT retryable"),
            (404, False, "404 Not Found is NOT retryable"),
            (422, False, "422 Unprocessable Entity is NOT retryable"),
        ],
    )
    def test_http_exception_categorization(
        self, status_code: int, should_retry: bool, description: str
    ) -> None:
        """Verify HTTPException status codes are correctly categorized.

        Catches: Retrying client errors (4xx) that will never succeed.
        """
        error = HTTPException(status_code=status_code, detail="Test error")
        assert is_retryable_error(error) == should_retry, description

    @pytest.mark.unit
    def test_botocore_error_is_retryable(self) -> None:
        """BotoCoreError (low-level network issues) should be retryable.

        Catches: Premature failures on AWS SDK network issues.
        """

        class MockBotoCoreError(BotoCoreError):
            fmt = "Test error"

        error = MockBotoCoreError()
        assert is_retryable_error(error) is True

    @pytest.mark.unit
    def test_unknown_error_is_not_retryable(self) -> None:
        """Unknown/unexpected errors should NOT be retryable.

        Catches: Infinite retries on programming errors or unexpected exceptions.
        """
        error: Exception = ValueError("Unexpected error")
        assert is_retryable_error(error) is False

        error = RuntimeError("Programming error")
        assert is_retryable_error(error) is False
