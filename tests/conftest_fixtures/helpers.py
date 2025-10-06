"""
Test assertion and setup helpers to reduce boilerplate.

Provides standardized assertions for:
- Job state verification
- S3 operation verification
- Redis operation verification
- Error scenario setup
"""

from typing import Any, Optional, Dict
from unittest.mock import AsyncMock, MagicMock
from botocore.exceptions import ClientError


def assert_job_state(
    job_data: Dict[str, Any],
    expected_status: str,
    expected_confidence: Optional[float] = None,
    expected_error: Optional[str] = None,
) -> None:
    """Assert job data has expected state.

    Args:
        job_data: Job data dictionary to verify
        expected_status: Expected job status string
        expected_confidence: Expected confidence score (optional)
        expected_error: Expected error message substring (optional)

    Raises:
        AssertionError: If job state doesn't match expectations
    """
    assert job_data["status"] == expected_status, (
        f"Expected status {expected_status}, got {job_data['status']}"
    )

    if expected_confidence is not None:
        assert job_data.get("confidence_score") == expected_confidence, (
            f"Expected confidence {expected_confidence}, got {job_data.get('confidence_score')}"
        )

    if expected_error is not None:
        error_message = job_data.get("error_message")
        assert error_message is not None, "Expected error message but got None"
        assert expected_error in error_message, (
            f"Expected '{expected_error}' in error message, got: {error_message}"
        )


def assert_s3_upload(
    mock_s3: MagicMock,
    expected_bucket: str,
    expected_key_prefix: Optional[str] = None,
    call_count: int = 1,
) -> None:
    """Assert S3 upload was called correctly.

    Args:
        mock_s3: Mock S3 client
        expected_bucket: Expected bucket name
        expected_key_prefix: Expected S3 key prefix (optional)
        call_count: Expected number of upload calls

    Raises:
        AssertionError: If S3 calls don't match expectations
    """
    if hasattr(mock_s3, "upload_fileobj"):
        assert mock_s3.upload_fileobj.call_count == call_count, (
            f"Expected {call_count} upload_fileobj calls, got {mock_s3.upload_fileobj.call_count}"
        )

        if call_count > 0 and expected_key_prefix:
            call_args = mock_s3.upload_fileobj.call_args
            assert call_args is not None, "Expected upload_fileobj to be called"

            # Check kwargs
            if call_args.kwargs:
                bucket = call_args.kwargs.get("Bucket")
                key = call_args.kwargs.get("Key")
            else:
                # Check positional args (Fileobj, Bucket, Key)
                bucket = call_args[0][1] if len(call_args[0]) > 1 else None
                key = call_args[0][2] if len(call_args[0]) > 2 else None

            assert bucket == expected_bucket, f"Expected bucket {expected_bucket}, got {bucket}"
            if expected_key_prefix:
                assert key.startswith(expected_key_prefix), (
                    f"Expected key to start with {expected_key_prefix}, got {key}"
                )


def assert_redis_set(
    mock_redis: AsyncMock,
    expected_key: str,
    call_count: int = 1,
) -> None:
    """Assert Redis set operation was called correctly.

    Args:
        mock_redis: Mock Redis client
        expected_key: Expected Redis key
        call_count: Expected number of set calls

    Raises:
        AssertionError: If Redis calls don't match expectations
    """
    assert mock_redis.set.call_count == call_count, (
        f"Expected {call_count} set calls, got {mock_redis.set.call_count}"
    )

    if call_count > 0:
        call_args = mock_redis.set.call_args
        assert call_args is not None, "Expected set to be called"

        # Check first positional arg (key)
        actual_key = call_args[0][0]
        assert actual_key == expected_key, (
            f"Expected key {expected_key}, got {actual_key}"
        )


def assert_redis_queue_push(
    mock_redis: AsyncMock,
    queue_name: str,
    call_count: int = 1,
    method: str = "lpush",
) -> None:
    """Assert Redis queue push operation was called correctly.

    Args:
        mock_redis: Mock Redis client
        queue_name: Expected queue name
        call_count: Expected number of push calls
        method: Queue method (lpush or rpush)

    Raises:
        AssertionError: If Redis calls don't match expectations
    """
    push_method = getattr(mock_redis, method)
    assert push_method.call_count == call_count, (
        f"Expected {call_count} {method} calls, got {push_method.call_count}"
    )

    if call_count > 0:
        call_args = push_method.call_args
        assert call_args is not None, f"Expected {method} to be called"

        # Check first positional arg (queue name)
        actual_queue = call_args[0][0]
        assert actual_queue == queue_name, (
            f"Expected queue {queue_name}, got {actual_queue}"
        )


def setup_s3_error(
    mock_s3: MagicMock,
    error_code: str = "NoSuchKey",
    method: str = "get_object",
) -> None:
    """Configure S3 mock to raise ClientError.

    Args:
        mock_s3: Mock S3 client
        error_code: AWS error code (NoSuchKey, AccessDenied, etc.)
        method: S3 method to configure (get_object, put_object, etc.)
    """
    error = ClientError(
        error_response={
            "Error": {
                "Code": error_code,
                "Message": f"Test {error_code} error",
            }
        },
        operation_name=method,
    )

    s3_method = getattr(mock_s3, method)
    s3_method.side_effect = error


def setup_redis_error(
    mock_redis: AsyncMock,
    error_type: type = ConnectionError,
    method: str = "get",
) -> None:
    """Configure Redis mock to raise error.

    Args:
        mock_redis: Mock Redis client
        error_type: Exception type to raise
        method: Redis method to configure (get, set, lpush, etc.)
    """
    redis_method = getattr(mock_redis, method)
    redis_method.side_effect = error_type("Test Redis error")


def setup_ai_service_error(
    mock_ai_service: AsyncMock,
    error_type: type = RuntimeError,
    method: str = "enhance_accessibility",
) -> None:
    """Configure AI service mock to raise error.

    Args:
        mock_ai_service: Mock AI service
        error_type: Exception type to raise
        method: AI method to configure
    """
    ai_method = getattr(mock_ai_service, method)
    ai_method.side_effect = error_type("Test AI service error")
