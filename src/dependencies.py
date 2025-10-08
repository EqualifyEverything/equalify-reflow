"""Dependency injection for shared clients and services."""

from typing import AsyncGenerator, Optional
import boto3
from botocore.config import Config
import redis.asyncio as redis
from fastapi import Depends

from .config import settings
from .services.storage_service import StorageService
from .services.queue_service import QueueService
from .services.job_service import JobService
from .services.rate_limit_service import RateLimitService


# Client dependencies with proper resource cleanup
async def get_s3_client():
    """Get S3 client (LocalStack or AWS) with optimized retry configuration.

    Configures boto3 with:
    - Adaptive retry mode (intelligent throttling and backoff)
    - Connection pooling (50 connections)
    - Reasonable timeouts (10s connect, 60s read)

    Yields:
        Configured boto3 S3 client with production-ready settings

    Note:
        This is an async generator for FastAPI dependency injection.
        The client will be properly closed after the request completes.
    """
    # Boto3 retry configuration for production resilience
    retry_config = Config(
        retries={
            'mode': 'adaptive',  # Smart retry with client-side rate limiting
            'max_attempts': 3,   # Max attempts (note: app-level retry adds more)
        },
        connect_timeout=10,      # Connection timeout (seconds)
        read_timeout=60,         # Read timeout (seconds)
        max_pool_connections=50, # Connection pool size
    )

    client = boto3.client(
        "s3",
        endpoint_url=settings.aws_endpoint_url,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        config=retry_config,
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
        await client.aclose()


# Service dependencies
async def get_storage_service(
    s3_client = Depends(get_s3_client)
) -> StorageService:
    """Get storage service instance.

    Args:
        s3_client: S3 client (auto-injected by FastAPI Depends)

    Returns:
        Configured StorageService instance

    Note:
        In FastAPI routes, use: storage_service: StorageService = Depends(get_storage_service)

        For workers, do NOT use this function. Instead:
        s3_client = await anext(get_s3_client())
        storage_service = StorageService(s3_client=s3_client, ...)
    """
    return StorageService(
        s3_client=s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


async def get_queue_service(
    redis_client = Depends(get_redis_client)
) -> QueueService:
    """Get queue service instance.

    Args:
        redis_client: Redis client (auto-injected by FastAPI Depends)

    Returns:
        Configured QueueService instance

    Note:
        In FastAPI routes, use: queue_service: QueueService = Depends(get_queue_service)

        For workers, do NOT use this function. Instead:
        redis_client = await anext(get_redis_client())
        queue_service = QueueService(redis_client=redis_client)
    """
    return QueueService(redis_client=redis_client)


async def get_job_service(
    redis_client = Depends(get_redis_client)
) -> JobService:
    """Get job service instance.

    Args:
        redis_client: Redis client (auto-injected by FastAPI Depends)

    Returns:
        Configured JobService instance

    Note:
        In FastAPI routes, use: job_service: JobService = Depends(get_job_service)

        For workers, do NOT use this function. Instead:
        redis_client = await anext(get_redis_client())
        job_service = JobService(redis_client=redis_client)
    """
    return JobService(redis_client=redis_client)


async def get_rate_limit_service(
    redis_client = Depends(get_redis_client)
) -> AsyncGenerator[RateLimitService, None]:
    """Get rate limit service instance.

    Args:
        redis_client: Redis client (auto-injected by FastAPI Depends)

    Yields:
        Configured RateLimitService instance

    Note:
        In FastAPI routes, use: rate_limiter: RateLimitService = Depends(get_rate_limit_service)

        For workers, do NOT use this function. Instead:
        redis_client = await anext(get_redis_client())
        rate_limiter = RateLimitService(redis=redis_client)
    """
    # RateLimitService expects 'redis' parameter, not 'redis_client'
    yield RateLimitService(redis=redis_client)