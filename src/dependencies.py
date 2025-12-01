"""Dependency injection for shared clients and services."""

from collections.abc import AsyncGenerator
from functools import lru_cache

import boto3
import redis.asyncio as redis
from botocore.config import Config
from fastapi import Depends

from .config import settings
from .services.correction_approval_service import CorrectionApprovalService
from .services.job_service import JobService
from .services.queue_service import QueueService
from .services.rate_limit_service import RateLimitService
from .services.storage_service import StorageService

# Singleton S3 client for connection reuse
_s3_client = None

@lru_cache
def _get_s3_client_singleton():
    """Create singleton S3 client for connection reuse across requests.

    In production AWS: Uses IAM role credentials (no keys needed)
    In local dev: Uses LocalStack endpoint with test credentials
    """
    retry_config = Config(
        retries={
            'mode': 'adaptive',
            'max_attempts': 3,
        },
        connect_timeout=10,
        read_timeout=60,
        max_pool_connections=50,
    )

    # Build kwargs - only include credentials if explicitly set (for LocalStack)
    kwargs = {
        "service_name": "s3",
        "region_name": settings.aws_region,
        "config": retry_config,
    }

    # Only set endpoint_url if configured (LocalStack)
    if settings.aws_endpoint_url:
        kwargs["endpoint_url"] = settings.aws_endpoint_url

    # Only set credentials if explicitly configured (LocalStack)
    # In production, boto3 uses IAM role automatically
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        # When using access keys (LocalStack), ignore AWS_PROFILE from environment
        # This prevents boto3 from trying to load a profile when we have explicit credentials
        import os
        os.environ.pop("AWS_PROFILE", None)

    return boto3.client(**kwargs)


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
        This returns a singleton client to enable connection pooling
        and circuit breaker state persistence across requests.
    """
    yield _get_s3_client_singleton()


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


# Singleton StorageService for circuit breaker persistence
@lru_cache
def _get_storage_service_singleton():
    """Create singleton StorageService for circuit breaker persistence."""
    return StorageService(
        s3_client=_get_s3_client_singleton(),
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


# Service dependencies
async def get_storage_service() -> StorageService:
    """Get storage service instance.

    Returns:
        Singleton StorageService instance with persistent circuit breakers

    Note:
        This returns a singleton service to ensure:
        - Circuit breaker state persists across requests
        - S3 connection pooling is effective
        - Health checks are fast and reliable

        In FastAPI routes, use: storage_service: StorageService = Depends(get_storage_service)

        For workers, do NOT use this function. Instead:
        s3_client = await anext(get_s3_client())
        storage_service = StorageService(s3_client=s3_client, ...)
    """
    return _get_storage_service_singleton()


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


async def get_correction_approval_service(
    redis: redis.Redis = Depends(get_redis_client),
    job_service: JobService = Depends(get_job_service),
    storage: StorageService = Depends(get_storage_service)
) -> CorrectionApprovalService:
    """Get correction approval service instance.

    Handles text correction approval workflow including token validation,
    decision processing, and markdown finalization.

    Args:
        redis: Redis client (injected)
        job_service: Job service (injected)
        storage: Storage service (injected)

    Returns:
        CorrectionApprovalService instance

    Note:
        In FastAPI routes, use:
            correction_approval: CorrectionApprovalService = Depends(
                get_correction_approval_service
            )

        For workers, do NOT use this function. Instead:
            redis_client = await anext(get_redis_client())
            job_service = JobService(redis_client=redis_client)
            storage_service = StorageService(...)
            correction_approval = CorrectionApprovalService(
                redis_client=redis_client,
                job_service=job_service,
                storage_service=storage_service
            )
    """
    return CorrectionApprovalService(
        redis_client=redis,
        job_service=job_service,
        storage_service=storage
    )
