"""Queue service for Redis operations."""

import json
import logging
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel

from ..config import settings
from ..shared.constants.redis_keys import timeout_key

logger = logging.getLogger(__name__)


class QueueService:
    """Service for managing Redis queue operations."""

    def __init__(self, redis_client):
        """Initialize queue service with Redis client.

        Args:
            redis_client: Redis async client instance
        """
        self.redis = redis_client
        self.pii_queue = settings.pii_queue_name

    async def queue_pii_job(self, job_id: str, s3_key: str) -> None:
        """
        Queue a job for PII scanning.

        Args:
            job_id: Unique job identifier
            s3_key: S3 key where document is stored
        """
        payload = {
            "job_id": job_id,
            "s3_key": s3_key,
            "created_at": datetime.utcnow().isoformat()
        }

        # Push to queue
        await self.redis.lpush(self.pii_queue, json.dumps(payload))

    async def check_queue_depth(self) -> int:
        """
        Check the current queue depth.

        Returns:
            Number of items in the PII queue
        """
        try:
            return await self.redis.llen(self.pii_queue)
        except Exception:
            return -1

    async def check_redis_connection(self) -> bool:
        """
        Check if Redis is accessible.

        Returns:
            True if Redis is accessible, False otherwise
        """
        try:
            await self.redis.ping()
            return True
        except Exception:
            return False

    async def enqueue(self, queue_name: str, payload: BaseModel) -> None:
        """
        Add job to specified queue.

        Args:
            queue_name: Name of the Redis queue
            payload: Pydantic model instance to enqueue

        Raises:
            Exception: If enqueue operation fails
        """
        try:
            # Serialize Pydantic model to JSON
            payload_json = payload.model_dump_json()

            # Push to Redis list (LPUSH adds to head)
            await self.redis.lpush(queue_name, payload_json)
        except Exception as e:
            raise Exception(f"Failed to enqueue job to {queue_name}: {str(e)}")

    async def dequeue(
        self,
        queue_name: str,
        timeout: int = 5,
        model_class: Optional[type[BaseModel]] = None
    ) -> Optional[dict]:
        """
        Pop job from queue with blocking timeout.

        Args:
            queue_name: Name of the Redis queue
            timeout: Blocking timeout in seconds (default: 5)
            model_class: Optional Pydantic model class for deserialization

        Returns:
            Deserialized payload as dict, or None if timeout

        Raises:
            Exception: If dequeue operation fails
        """
        try:
            # BRPOP blocks until item available or timeout
            result = await self.redis.brpop(queue_name, timeout=timeout)

            if result is None:
                return None

            # result is tuple: (queue_name, value)
            _, payload_json = result

            # Deserialize JSON
            payload_dict = json.loads(payload_json)

            # Optionally validate with Pydantic model
            if model_class:
                validated = model_class(**payload_dict)
                return validated.model_dump()

            return payload_dict
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to deserialize queue payload: {str(e)}")
        except Exception as e:
            raise Exception(f"Failed to dequeue job from {queue_name}: {str(e)}")

    async def queue_depth(self, queue_name: str) -> int:
        """
        Get current queue depth.

        Args:
            queue_name: Name of the Redis queue

        Returns:
            Number of items in queue, or -1 on error
        """
        try:
            return await self.redis.llen(queue_name)
        except Exception:
            return -1

    async def peek_queue(
        self,
        queue_name: str,
        count: int = 10
    ) -> List[dict]:
        """
        View queued jobs without removing them.

        Args:
            queue_name: Name of the Redis queue
            count: Maximum number of items to peek (default: 10)

        Returns:
            List of deserialized payloads

        Raises:
            Exception: If peek operation fails
        """
        try:
            # LRANGE gets items without removing (0 is start, -1 would be end)
            items = await self.redis.lrange(queue_name, 0, count - 1)

            # Deserialize each item
            payloads = []
            for item in items:
                try:
                    payload = json.loads(item)
                    payloads.append(payload)
                except json.JSONDecodeError:
                    # Skip invalid items
                    continue

            return payloads
        except Exception as e:
            raise Exception(f"Failed to peek queue {queue_name}: {str(e)}")

    async def health_check(self) -> bool:
        """
        Check Redis connectivity.

        Returns:
            True if Redis is healthy, False otherwise
        """
        return await self.check_redis_connection()

    async def add_to_timeout_tracking(
        self,
        job_id: str,
        expires_at: datetime,
        timeout_type: str = "approval"
    ) -> None:
        """Add job to timeout tracking sorted set.

        Uses Redis sorted set (ZADD) where:
        - Score: Unix timestamp of expiration deadline
        - Member: job_id

        This allows efficient range queries to find expired jobs using ZRANGEBYSCORE.

        Args:
            job_id: Job identifier (UUID)
            expires_at: Datetime when approval expires
            timeout_type: Type of timeout (default: "approval")

        Raises:
            Exception: If Redis operation fails

        Example:
            >>> queue = QueueService(redis_client)
            >>> expires = datetime.now(timezone.utc) + timedelta(hours=4)
            >>> await queue.add_to_timeout_tracking("abc-123", expires)
        """
        try:
            # Convert datetime to Unix timestamp
            timestamp = expires_at.timestamp()

            # Get Redis key for this timeout type
            key = timeout_key(timeout_type)

            # ZADD adds to sorted set: ZADD key score member
            await self.redis.zadd(key, {job_id: timestamp})

            logger.debug(
                f"Added job {job_id} to timeout tracking "
                f"(expires at {expires_at.isoformat()})"
            )

        except Exception as e:
            logger.error(
                f"Failed to add job {job_id} to timeout tracking: {str(e)}",
                exc_info=True
            )
            raise Exception(f"Failed to add timeout tracking: {str(e)}")

    async def get_expired_timeouts(
        self,
        timeout_type: str = "approval"
    ) -> list[tuple[str, float]]:
        """Get jobs with expired approval deadlines.

        Queries Redis sorted set for members with scores (timestamps) less than
        or equal to current time. Returns job IDs and their expiration timestamps.

        Args:
            timeout_type: Type of timeout to check (default: "approval")

        Returns:
            List of tuples: [(job_id, expiration_timestamp), ...]
            Empty list if no expired jobs or on error

        Example:
            >>> queue = QueueService(redis_client)
            >>> expired = await queue.get_expired_timeouts()
            >>> for job_id, timestamp in expired:
            ...     print(f"Job {job_id} expired at {datetime.fromtimestamp(timestamp)}")
        """
        try:
            # Get current timestamp
            current_time = datetime.utcnow().timestamp()

            # Get Redis key for this timeout type
            key = timeout_key(timeout_type)

            # ZRANGEBYSCORE returns members with score in range [0, current_time]
            # withscores=True returns (member, score) tuples
            result = await self.redis.zrangebyscore(
                key,
                min=0,
                max=current_time,
                withscores=True
            )

            # Convert result to list of tuples (job_id, timestamp)
            expired_jobs = []
            for job_id, timestamp in result:
                # Redis returns bytes, decode to string
                if isinstance(job_id, bytes):
                    job_id = job_id.decode('utf-8')
                expired_jobs.append((job_id, float(timestamp)))

            if expired_jobs:
                logger.info(
                    f"Found {len(expired_jobs)} expired {timeout_type} timeouts"
                )

            return expired_jobs

        except Exception as e:
            logger.error(
                f"Failed to get expired {timeout_type} timeouts: {str(e)}",
                exc_info=True
            )
            # Return empty list on error (fail-safe)
            return []

    async def remove_from_timeout_tracking(
        self,
        job_id: str,
        timeout_type: str = "approval"
    ) -> bool:
        """Remove job from timeout tracking sorted set.

        Called when:
        - Job is approved/denied (no longer needs timeout tracking)
        - Timeout has been processed
        - Job is completed or failed

        Args:
            job_id: Job identifier (UUID)
            timeout_type: Type of timeout (default: "approval")

        Returns:
            bool: True if job was removed, False if job wasn't in set

        Example:
            >>> queue = QueueService(redis_client)
            >>> removed = await queue.remove_from_timeout_tracking("abc-123")
            >>> if removed:
            ...     print("Job removed from timeout tracking")
        """
        try:
            # Get Redis key for this timeout type
            key = timeout_key(timeout_type)

            # ZREM removes member from sorted set
            # Returns number of members removed (0 or 1)
            count = await self.redis.zrem(key, job_id)

            if count > 0:
                logger.debug(
                    f"Removed job {job_id} from {timeout_type} timeout tracking"
                )
                return True
            else:
                logger.debug(
                    f"Job {job_id} was not in {timeout_type} timeout tracking"
                )
                return False

        except Exception as e:
            logger.error(
                f"Failed to remove job {job_id} from timeout tracking: {str(e)}",
                exc_info=True
            )
            # Return False on error (idempotent - job effectively not tracked)
            return False

    async def get_timeout_count(self, timeout_type: str = "approval") -> int:
        """Get count of jobs currently in timeout tracking.

        Args:
            timeout_type: Type of timeout (default: "approval")

        Returns:
            int: Number of jobs in timeout tracking, or -1 on error

        Example:
            >>> queue = QueueService(redis_client)
            >>> count = await queue.get_timeout_count()
            >>> print(f"{count} jobs awaiting approval")
        """
        try:
            key = timeout_key(timeout_type)
            count = await self.redis.zcard(key)
            return count
        except Exception as e:
            logger.error(
                f"Failed to get timeout count: {str(e)}",
                exc_info=True
            )
            return -1