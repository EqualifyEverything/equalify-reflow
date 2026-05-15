"""Health check endpoints.

Two endpoints with deliberately different contracts:

* ``GET /health`` is **tolerant**. It returns 200 with status
  ``healthy``/``degraded`` when the api process is serving traffic and the
  core stores (Redis, S3) are reachable, even if docling-serve is briefly
  unavailable. The docling-serve client has retry + a circuit breaker for
  short outages, and CPU model load at boot takes a few minutes — failing
  this endpoint during that window would cause restart loops. Returns 503
  only when a core store is gone.

* ``GET /health/ready`` is **strict**. It returns 503 the moment *any*
  dependency the request path needs is unhealthy, including docling-serve.
  Wire orchestrator readiness probes (Kubernetes readinessProbe, ECS ALB
  target groups) here when you want traffic to stop the instant the
  pipeline can't process documents end to end.

Pair the two for the conventional k8s split: ``/health`` for liveness,
``/health/ready`` for readiness.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from ..dependencies import get_queue_service, get_storage_service
from ..services import QueueService, StorageService

router = APIRouter(prefix="/health", tags=["Health"])


async def _collect_checks(
    storage: StorageService, queue: QueueService
) -> dict[str, Any]:
    """Run every dependency check and return a structured result.

    Shared by both endpoints so they cannot drift in what they probe.
    """
    try:
        from ..services.docling_serve_client import get_docling_client
        docling_client = get_docling_client()
        docling_healthy = await docling_client.check_health()
    except RuntimeError:
        # Client not yet initialised (boot race)
        docling_healthy = False

    return {
        "redis": await queue.check_redis_connection(),
        "s3": await storage.check_s3_access(),
        "queue_depth": await queue.check_queue_depth(),
        "docling_serve": docling_healthy,
    }


@router.get("")
async def health_check(
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service)
) -> dict[str, Any]:
    """Tolerant health endpoint suitable for dashboards and liveness probes.

    Returns 200 ``healthy`` when every check passes, 200 ``degraded`` when
    only docling-serve is down (boot warmup or transient outage that retry
    + circuit-breaker can absorb), and 503 ``unhealthy`` when a core store
    (Redis, S3, queue) is unreachable.

    Use ``/health/ready`` for an unforgiving readiness probe.
    """
    checks = await _collect_checks(storage, queue)
    core_healthy = checks["redis"] and checks["s3"] and checks["queue_depth"] >= 0
    if core_healthy:
        return {
            "status": "healthy" if checks["docling_serve"] else "degraded",
            "checks": checks,
        }
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "unhealthy", "checks": checks},
    )


@router.get("/ready")
async def readiness_check(
    storage: StorageService = Depends(get_storage_service),
    queue: QueueService = Depends(get_queue_service)
) -> dict[str, Any]:
    """Strict readiness probe for orchestrators.

    Returns 200 ``ready`` only when every dependency the pipeline needs is
    reachable: Redis, S3, queue, and docling-serve. Any failure returns
    503 so the orchestrator stops routing traffic until end-to-end
    document processing can succeed again.

    Configure Kubernetes ``readinessProbe`` or ECS ALB target groups
    against this endpoint when you want traffic gated on full pipeline
    health. Use ``/health`` (tolerant) for liveness alongside.
    """
    checks = await _collect_checks(storage, queue)
    all_ready = (
        checks["redis"]
        and checks["s3"]
        and checks["queue_depth"] >= 0
        and checks["docling_serve"]
    )
    if all_ready:
        return {"status": "ready", "checks": checks}
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"status": "not_ready", "checks": checks},
    )
