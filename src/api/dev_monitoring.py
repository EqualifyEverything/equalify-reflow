"""
Development-only monitoring endpoints for demo UI.

SECURITY: Only available when ENVIRONMENT=dev
"""

from datetime import datetime
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Depends

from ..config import settings
from ..dependencies import get_queue_service, get_redis_client
from ..services.queue_service import QueueService
from ..shared.constants.queues import PII_QUEUE, PROCESSING_QUEUE, APPROVAL_TIMEOUT_KEY
from redis.asyncio import Redis

router = APIRouter(prefix="/api/dev", tags=["Development"])


def require_dev_mode() -> None:
    """Ensure endpoint only accessible in development."""
    if settings.environment != "dev":
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/monitoring/queues")
async def get_queue_metrics(
    queue: QueueService = Depends(get_queue_service),
    redis: Redis = Depends(get_redis_client),
) -> Dict[str, Any]:
    """
    Get queue depths for development dashboard.

    Returns queue depths for:
    - PII scan queue
    - Processing queue
    - Approval pending queue (sorted set)
    """
    require_dev_mode()

    # Get queue lengths
    pii_scan_depth = await redis.llen(PII_QUEUE)
    processing_depth = await redis.llen(PROCESSING_QUEUE)

    # For sorted sets (approval timeouts), use ZCARD
    approval_depth = await redis.zcard(APPROVAL_TIMEOUT_KEY)

    return {
        "queues": {
            "pii_scan": pii_scan_depth,
            "processing": processing_depth,
            "approval_pending": approval_depth,
        },
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
