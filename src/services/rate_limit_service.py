"""Rate limiting service using Redis sliding window algorithm."""

import logging
import time
import uuid

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RateLimitService:
    """
    Redis-based rate limiting with sliding window algorithm.

    Supports multiple rate limit tiers:
    - Per-IP submission limits (prevent abuse)
    - Per-IP status check limits (prevent polling storms)
    - Global submission limits (cost control)
    """

    def __init__(self, redis: Redis):
        """
        Initialize rate limit service.

        Args:
            redis: Redis connection
        """
        self.redis = redis

        # Rate limit configurations (requests per window)
        self.SUBMIT_PER_IP_LIMIT = 10  # 10 submissions per hour per IP
        self.SUBMIT_PER_IP_WINDOW = 3600  # 1 hour in seconds

        self.STATUS_PER_IP_LIMIT = 100  # 100 status checks per hour per IP
        self.STATUS_PER_IP_WINDOW = 3600  # 1 hour in seconds

        self.GLOBAL_SUBMIT_LIMIT = 1000  # 1000 submissions per day (cost control)
        self.GLOBAL_SUBMIT_WINDOW = 86400  # 24 hours in seconds

    async def check_submit_rate_limit(self, client_ip: str) -> tuple[bool, int | None]:
        """
        Check if client can submit a document.

        Uses sliding window algorithm with two checks:
        1. Per-IP limit (prevent individual abuse)
        2. Global limit (prevent system-wide cost overrun)

        Args:
            client_ip: Client IP address

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
            - (True, None) if allowed
            - (False, seconds) if rate limited
        """
        # Check per-IP limit
        ip_allowed, ip_retry = await self._check_rate_limit(
            key=f"eq-pdf:ratelimit:submit:ip:{client_ip}",
            limit=self.SUBMIT_PER_IP_LIMIT,
            window=self.SUBMIT_PER_IP_WINDOW
        )

        if not ip_allowed:
            logger.warning(
                f"Rate limit exceeded for IP {client_ip}: "
                f"{self.SUBMIT_PER_IP_LIMIT} submissions/{self.SUBMIT_PER_IP_WINDOW}s"
            )
            return False, ip_retry

        # Check global limit
        global_allowed, global_retry = await self._check_rate_limit(
            key="eq-pdf:ratelimit:submit:global",
            limit=self.GLOBAL_SUBMIT_LIMIT,
            window=self.GLOBAL_SUBMIT_WINDOW
        )

        if not global_allowed:
            logger.warning(
                f"Global rate limit exceeded: "
                f"{self.GLOBAL_SUBMIT_LIMIT} submissions/{self.GLOBAL_SUBMIT_WINDOW}s"
            )
            return False, global_retry

        return True, None

    async def check_status_rate_limit(self, client_ip: str) -> tuple[bool, int | None]:
        """
        Check if client can check job status.

        Prevents polling storms from aggressive clients.

        Args:
            client_ip: Client IP address

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        allowed, retry_after = await self._check_rate_limit(
            key=f"eq-pdf:ratelimit:status:ip:{client_ip}",
            limit=self.STATUS_PER_IP_LIMIT,
            window=self.STATUS_PER_IP_WINDOW
        )

        if not allowed:
            logger.warning(
                f"Status check rate limit exceeded for IP {client_ip}: "
                f"{self.STATUS_PER_IP_LIMIT} checks/{self.STATUS_PER_IP_WINDOW}s"
            )

        return allowed, retry_after

    async def _check_rate_limit(
        self,
        key: str,
        limit: int,
        window: int
    ) -> tuple[bool, int | None]:
        """
        Sliding window rate limit check using Redis.

        Algorithm:
        1. Use current timestamp as score
        2. Remove expired entries (outside window)
        3. Count entries in window
        4. If under limit, add new entry
        5. Return whether request is allowed

        Args:
            key: Redis key for this rate limit
            limit: Maximum requests in window
            window: Time window in seconds

        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        now = time.time()
        window_start = now - window

        try:
            # Use Redis pipeline for atomic operations
            pipe = self.redis.pipeline()

            # Remove entries outside the window
            pipe.zremrangebyscore(key, 0, window_start)

            # Count current entries in window
            pipe.zcard(key)

            # Execute pipeline
            results = await pipe.execute()
            current_count = results[1]

            # Check if under limit
            if current_count < limit:
                # Add this request to the window
                # Use unique member to prevent collisions at the same microsecond
                member = f"{now}-{uuid.uuid4().hex[:8]}"
                await self.redis.zadd(key, {member: now})

                # Set expiry on the key (cleanup)
                await self.redis.expire(key, window)

                return True, None
            else:
                # Calculate retry-after based on oldest entry
                oldest_entries = await self.redis.zrange(key, 0, 0, withscores=True)
                if oldest_entries:
                    oldest_timestamp = oldest_entries[0][1]
                    retry_after = int(oldest_timestamp + window - now)
                    return False, max(1, retry_after)  # At least 1 second
                else:
                    return False, window

        except Exception as e:
            logger.error(f"Rate limit check failed: {e}", exc_info=True)
            # Fail open (allow request) if Redis is down
            # This prevents rate limiter from being a single point of failure
            return True, None

    async def get_remaining_quota(self, client_ip: str, operation: str = "submit") -> dict:
        """
        Get remaining quota for a client.

        Useful for informative API responses.

        Args:
            client_ip: Client IP address
            operation: "submit" or "status"

        Returns:
            Dict with quota information
        """
        if operation == "submit":
            key = f"eq-pdf:ratelimit:submit:ip:{client_ip}"
            limit = self.SUBMIT_PER_IP_LIMIT
            window = self.SUBMIT_PER_IP_WINDOW
        else:
            key = f"eq-pdf:ratelimit:status:ip:{client_ip}"
            limit = self.STATUS_PER_IP_LIMIT
            window = self.STATUS_PER_IP_WINDOW

        try:
            now = time.time()
            window_start = now - window

            # Count current entries
            await self.redis.zremrangebyscore(key, 0, window_start)
            current = await self.redis.zcard(key)

            remaining = min(limit, max(0, limit - current))

            # Calculate reset time
            if current > 0:
                oldest = await self.redis.zrange(key, 0, 0, withscores=True)
                reset_at = int(oldest[0][1] + window) if oldest else int(now + window)
            else:
                reset_at = int(now + window)

            return {
                "limit": limit,
                "remaining": remaining,
                "reset_at": reset_at,
                "window_seconds": window
            }

        except Exception as e:
            logger.error(f"Failed to get quota info: {e}", exc_info=True)
            return {
                "limit": limit,
                "remaining": limit,  # Fail open
                "reset_at": int(time.time() + window),
                "window_seconds": window
            }

    async def reset_rate_limit(self, client_ip: str, operation: str = "submit") -> bool:
        """
        Reset rate limit for a client.

        Administrative function for manual intervention.

        Args:
            client_ip: Client IP address
            operation: "submit" or "status"

        Returns:
            True if successful
        """
        if operation == "submit":
            key = f"eq-pdf:ratelimit:submit:ip:{client_ip}"
        else:
            key = f"eq-pdf:ratelimit:status:ip:{client_ip}"

        try:
            await self.redis.delete(key)
            logger.info(f"Reset rate limit for {client_ip} ({operation})")
            return True
        except Exception as e:
            logger.error(f"Failed to reset rate limit: {e}", exc_info=True)
            return False
