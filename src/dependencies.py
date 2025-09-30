"""Dependency injection for shared clients and services."""

from typing import AsyncGenerator
import boto3
import redis.asyncio as redis

from .config import settings
from .services.storage_service import StorageService
from .services.queue_service import QueueService
from .services.job_service import JobService


# Client dependencies with proper resource cleanup
async def get_s3_client():
    """Get S3 client (LocalStack or AWS) with resource cleanup.

    Yields:
        Configured boto3 S3 client

    Note:
        This is an async generator for FastAPI dependency injection.
        The client will be properly closed after the request completes.
    """
    client = boto3.client(
        "s3",
        endpoint_url=settings.aws_endpoint_url,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    try:
        yield client
    finally:
        # Boto3 client doesn't need explicit closing in sync mode
        pass


async def get_redis_client() -> AsyncGenerator[redis.Redis, None]:
    """Get Redis client with connection pool and cleanup.

    Yields:
        Configured Redis async client

    Note:
        This is an async generator for FastAPI dependency injection.
        The connection pool will be properly closed after the request completes.
    """
    client = redis.from_url(
        settings.redis_url,
        decode_responses=True,
        max_connections=settings.redis_max_connections,
    )
    try:
        yield client
    finally:
        await client.close()


# Service dependencies
async def get_storage_service(
    s3_client=None
) -> StorageService:
    """Get storage service instance.

    Args:
        s3_client: Optional S3 client (injected by FastAPI Depends)

    Returns:
        Configured StorageService instance

    Note:
        In FastAPI routes, use: storage_service: StorageService = Depends(get_storage_service)
    """
    if s3_client is None:
        # For non-FastAPI usage (workers, tests)
        s3_client = boto3.client(
            "s3",
            endpoint_url=settings.aws_endpoint_url,
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

    return StorageService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


async def get_queue_service(
    redis_client=None
) -> QueueService:
    """Get queue service instance.

    Args:
        redis_client: Optional Redis client (injected by FastAPI Depends)

    Returns:
        Configured QueueService instance

    Note:
        In FastAPI routes, use: queue_service: QueueService = Depends(get_queue_service)
    """
    if redis_client is None:
        # For non-FastAPI usage (workers, tests)
        redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
        )

    return QueueService(redis_client=redis_client)


async def get_job_service(
    redis_client=None
) -> JobService:
    """Get job service instance.

    Args:
        redis_client: Optional Redis client (injected by FastAPI Depends)

    Returns:
        Configured JobService instance

    Note:
        In FastAPI routes, use: job_service: JobService = Depends(get_job_service)
    """
    if redis_client is None:
        # For non-FastAPI usage (workers, tests)
        redis_client = redis.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=settings.redis_max_connections,
        )

    return JobService(redis_client=redis_client)