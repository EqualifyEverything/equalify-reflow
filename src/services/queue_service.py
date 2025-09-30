"""Queue service for Redis operations."""

import json
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel

from ..config import settings


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