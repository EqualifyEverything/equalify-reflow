"""Health check endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import get_queue_service, get_storage_service
from ..services import QueueService, StorageService

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check(
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service)
):
    """
    Health check endpoint for container orchestration.

    Checks Redis, S3, and queue connectivity.

    Args:
        storage: Storage service (injected)
        queue: Queue service (injected)

    Returns:
        Health status with detailed checks
    """

    checks = {
        "redis": await queue.check_redis_connection(),
        "s3": await storage.check_s3_access(),
        "queue_depth": await queue.check_queue_depth()
    }

    # All checks must pass
    if checks["redis"] and checks["s3"] and checks["queue_depth"] >= 0:
        return {
            "status": "healthy",
            "checks": checks
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "checks": checks
            }
        )


@router.get("/ready")
async def readiness_check():
    """
    Readiness check for Kubernetes/orchestration.

    Returns:
        Ready status
    """
    return {"status": "ready"}
