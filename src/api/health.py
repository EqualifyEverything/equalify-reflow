"""Health check endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import get_queue_service, get_storage_service
from ..services import QueueService, StorageService

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check(
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service)
) -> dict[str, Any]:
    """
    Health check endpoint for container orchestration.

    Checks Redis, S3, and queue connectivity.

    Args:
        storage: Storage service (injected)
        queue: Queue service (injected)

    Returns:
        Health status with detailed checks
    """

    # Check docling-serve health
    try:
        from ..services.docling_serve_client import get_docling_client
        docling_client = get_docling_client()
        docling_healthy = await docling_client.check_health()
    except RuntimeError:
        docling_healthy = False

    checks = {
        "redis": await queue.check_redis_connection(),
        "s3": await storage.check_s3_access(),
        "queue_depth": await queue.check_queue_depth(),
        "docling_serve": docling_healthy,
    }

    # All checks must pass
    if checks["redis"] and checks["s3"] and checks["queue_depth"] >= 0 and checks["docling_serve"]:
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
async def readiness_check() -> dict[str, str]:
    """
    Readiness check for Kubernetes/orchestration.

    Returns:
        Ready status
    """
    return {"status": "ready"}
