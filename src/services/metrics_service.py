"""Service for tracking and managing cleanup metrics.

This service stores metrics about timeout worker operations
(timeouts processed, files cleaned, orphans detected, etc.)
in Redis hashes organized by date.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from ..config import settings

logger = logging.getLogger(__name__)


class MetricsService:
    """Service for metrics tracking and cleanup."""

    # Redis key prefix for daily metrics
    METRICS_PREFIX = "eq-pdf:metrics:daily:"

    def __init__(self, redis_client):
        """Initialize metrics service.

        Args:
            redis_client: Redis async client instance
        """
        self.redis = redis_client

    def _get_metrics_key(self, date: Optional[datetime] = None) -> str:
        """Get Redis key for metrics on a specific date.

        Args:
            date: Date for metrics (defaults to today)

        Returns:
            Redis key for daily metrics
        """
        if date is None:
            date = datetime.now(timezone.utc)

        date_str = date.strftime("%Y%m%d")
        return f"{self.METRICS_PREFIX}{date_str}"

    async def increment_metric(self, metric_name: str, value: int = 1) -> None:
        """Increment a metric counter for today.

        Args:
            metric_name: Name of metric to increment
            value: Value to increment by (default: 1)
        """
        try:
            key = self._get_metrics_key()

            # Increment the metric field in today's hash
            await self.redis.hincrby(key, metric_name, value)

            # Set expiration on the key (auto-cleanup after retention period + buffer)
            ttl_days = settings.metrics_retention_days + 7  # 7 day buffer
            await self.redis.expire(key, ttl_days * 24 * 60 * 60)

            logger.debug(f"Incremented metric {metric_name} by {value}")

        except Exception as e:
            logger.error(
                f"Failed to increment metric {metric_name}: {e}",
                exc_info=True
            )
            # Don't raise - metrics failures shouldn't break operations

    async def get_metric(
        self,
        metric_name: str,
        date: Optional[datetime] = None
    ) -> int:
        """Get value of a metric for a specific date.

        Args:
            metric_name: Name of metric to retrieve
            date: Date for metrics (defaults to today)

        Returns:
            Metric value (0 if not found)
        """
        try:
            key = self._get_metrics_key(date)
            value = await self.redis.hget(key, metric_name)

            if value is None:
                return 0

            return int(value)

        except Exception as e:
            logger.error(
                f"Failed to get metric {metric_name}: {e}",
                exc_info=True
            )
            return 0

    async def get_all_metrics(
        self,
        date: Optional[datetime] = None
    ) -> Dict[str, int]:
        """Get all metrics for a specific date.

        Args:
            date: Date for metrics (defaults to today)

        Returns:
            Dictionary of metric names to values
        """
        try:
            key = self._get_metrics_key(date)
            metrics = await self.redis.hgetall(key)

            if not metrics:
                return {}

            # Convert values to integers
            return {k: int(v) for k, v in metrics.items()}

        except Exception as e:
            logger.error(f"Failed to get all metrics: {e}", exc_info=True)
            return {}

    async def cleanup_old_metrics(self) -> int:
        """Delete metrics older than retention period.

        Returns:
            Number of metric keys deleted
        """
        try:
            # Calculate cutoff date
            cutoff_date = datetime.now(timezone.utc) - timedelta(
                days=settings.metrics_retention_days
            )

            logger.info(
                f"Starting metrics cleanup (retention: {settings.metrics_retention_days} days, "
                f"cutoff: {cutoff_date.strftime('%Y-%m-%d')})"
            )

            # Scan for all metric keys
            pattern = f"{self.METRICS_PREFIX}*"
            deleted_count = 0

            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100
                )

                for key in keys:
                    try:
                        # Extract date from key (format: eq-pdf:metrics:daily:YYYYMMDD)
                        date_str = key.split(":")[-1]
                        key_date = datetime.strptime(date_str, "%Y%m%d").replace(
                            tzinfo=timezone.utc
                        )

                        # Delete if older than cutoff
                        if key_date < cutoff_date:
                            await self.redis.delete(key)
                            deleted_count += 1
                            logger.debug(f"Deleted old metrics key: {key}")

                    except (ValueError, IndexError) as e:
                        logger.warning(f"Invalid metrics key format: {key}, error: {e}")

                if cursor == 0:
                    break

            logger.info(f"Metrics cleanup complete: {deleted_count} keys deleted")
            return deleted_count

        except Exception as e:
            logger.error(f"Error during metrics cleanup: {e}", exc_info=True)
            return 0

    async def log_daily_summary(self) -> None:
        """Log summary of today's metrics (for debugging/monitoring).

        This method is useful for daily health checks and debugging.
        """
        try:
            metrics = await self.get_all_metrics()

            if not metrics:
                logger.info("Daily metrics summary: No metrics recorded today")
                return

            # Format metrics for logging
            summary_lines = ["Daily metrics summary:"]
            for metric_name, value in sorted(metrics.items()):
                summary_lines.append(f"  {metric_name}: {value}")

            logger.info("\n".join(summary_lines))

        except Exception as e:
            logger.error(f"Failed to log daily summary: {e}", exc_info=True)
