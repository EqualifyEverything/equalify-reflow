"""Job service for job status management."""

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from ..config import settings
from ..shared.constants.redis_keys import JOBS_BY_UPDATED
from ..shared.constants.statuses import STATUS_COMPLETED, STATUS_DENIED, STATUS_FAILED
from ..utils.token_generator import generate_secure_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lua scripts – executed atomically by Redis to avoid race conditions between
# HSET, EXPIRE, and ZADD when the process could crash between calls.
# ---------------------------------------------------------------------------

# Atomically: HSET fields + EXPIRE ttl + ZADD to secondary index
# KEYS[1] = job hash key, KEYS[2] = jobs-by-updated sorted set key
# ARGV[1] = ttl, ARGV[2] = timestamp (score), ARGV[3] = job_id
# ARGV[4..N] = field/value pairs (must be even count)
_LUA_HSET_EXPIRE_ZADD = """
local key = KEYS[1]
local idx = KEYS[2]
local ttl = tonumber(ARGV[1])
local ts  = tonumber(ARGV[2])
local jid = ARGV[3]
for i = 4, #ARGV, 2 do
    redis.call('HSET', key, ARGV[i], ARGV[i+1])
end
redis.call('EXPIRE', key, ttl)
redis.call('ZADD', idx, ts, jid)
return 1
"""

# Atomically: HSET fields + ZADD to secondary index (no TTL change)
# KEYS[1] = job hash key, KEYS[2] = jobs-by-updated sorted set key
# ARGV[1] = timestamp (score), ARGV[2] = job_id
# ARGV[3..N] = field/value pairs
_LUA_HSET_ZADD = """
local key = KEYS[1]
local idx = KEYS[2]
local ts  = tonumber(ARGV[1])
local jid = ARGV[2]
for i = 3, #ARGV, 2 do
    redis.call('HSET', key, ARGV[i], ARGV[i+1])
end
redis.call('ZADD', idx, ts, jid)
return 1
"""

# Atomically: DEL job hash + ZREM from secondary index
# KEYS[1] = job hash key, KEYS[2] = jobs-by-updated sorted set key
# ARGV[1] = job_id
_LUA_DELETE_JOB = """
local deleted = redis.call('DEL', KEYS[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return deleted
"""


class JobService:
    """Service for managing job status and metadata."""

    def __init__(self, redis_client: Redis) -> None:
        """Initialize job service with Redis client.

        Args:
            redis_client: Redis async client instance
        """
        self.redis = redis_client
        self.status_prefix = settings.job_status_prefix
        self.jobs_index_key = JOBS_BY_UPDATED
        # TTL settings from config
        self.job_ttl_active = settings.job_ttl_active
        self.job_ttl_completed = settings.job_ttl_completed
        self.job_ttl_failed = settings.job_ttl_failed
        self.job_ttl_denied = settings.job_ttl_denied

        # Register Lua scripts (sync – returns callable Script objects)
        self._script_hset_expire_zadd = self.redis.register_script(_LUA_HSET_EXPIRE_ZADD)
        self._script_hset_zadd = self.redis.register_script(_LUA_HSET_ZADD)
        self._script_delete_job = self.redis.register_script(_LUA_DELETE_JOB)

    def _get_ttl_for_status(self, status: str) -> int:
        """Get appropriate TTL (time-to-live) for job based on status.

        Different statuses have different retention requirements:
        - Completed jobs: 30 days (for result retrieval and audit)
        - Failed jobs: 30 days (for debugging and retry decisions)
        - Denied jobs: 7 days (shorter retention, decision recorded)
        - Active jobs (processing, awaiting_approval, etc.): 7 days

        Args:
            status: Job status string

        Returns:
            TTL in seconds for the given status

        Example:
            >>> ttl = self._get_ttl_for_status("completed")
            >>> print(f"Completed jobs expire after {ttl / 86400} days")
        """
        if status == STATUS_COMPLETED:
            return self.job_ttl_completed
        elif status == STATUS_FAILED:
            return self.job_ttl_failed
        elif status == STATUS_DENIED:
            return self.job_ttl_denied
        else:
            # Default for active states: pii_scanning, awaiting_approval, processing
            return self.job_ttl_active

    @staticmethod
    def _flatten_mapping(mapping: dict) -> list[str]:
        """Flatten a dict to [field, value, field, value, ...] for Lua scripts."""
        flat: list[str] = []
        for k, v in mapping.items():
            flat.append(str(k))
            flat.append(str(v))
        return flat

    async def create_job(
        self,
        job_id: str,
        s3_key: str,
        status: str = "pii_scanning",
        original_filename: str | None = None,
        pii_skipped: bool = False,
        pii_skip_reason: str | None = None,
        debug_bundle_requested: bool = False,
        review_mode: str | None = None,
        max_rounds: int = 1,
    ) -> None:
        """
        Create a new job in Redis with automatic TTL.

        Atomically sets hash fields, TTL, and secondary index via Lua script
        to prevent memory leaks if the process crashes between operations.

        Args:
            job_id: Unique job identifier
            s3_key: S3 key where document is stored
            status: Initial job status (default: "pii_scanning")
            original_filename: Original filename from upload
            pii_skipped: Whether PII scan was skipped (for audit trail)
            pii_skip_reason: Reason PII scan was skipped
            debug_bundle_requested: Whether to generate debug bundle artifacts
            review_mode: Review mode ('auto' | 'human') for agentic pipeline
            max_rounds: Maximum number of iterative refinement rounds (1-5, default: 1)
        """
        created_at = datetime.now(UTC).isoformat()

        mapping: dict[str, str] = {
            "job_id": job_id,
            "s3_key": s3_key,
            "status": status,
            "created_at": created_at,
            "updated_at": created_at,
            "max_rounds": str(max_rounds),
        }
        if original_filename:
            mapping["original_filename"] = original_filename

        # Add PII skip audit fields if applicable
        if pii_skipped:
            mapping["pii_skipped"] = "true"
            if pii_skip_reason:
                mapping["pii_skip_reason"] = pii_skip_reason

        # Add debug bundle flag
        if debug_bundle_requested:
            mapping["debug_bundle_requested"] = "true"

        # Add review mode for agentic pipeline
        if review_mode:
            mapping["review_mode"] = review_mode

        ttl = self._get_ttl_for_status(status)
        key = f"{self.status_prefix}{job_id}"
        now_ts = time.time()

        await self._script_hset_expire_zadd(
            keys=[key, self.jobs_index_key],
            args=[str(ttl), str(now_ts), job_id] + self._flatten_mapping(mapping),
        )

    async def get_job(self, job_id: str) -> dict[str, Any] | None:
        """
        Get job status and metadata.

        Args:
            job_id: Job identifier

        Returns:
            Job data dictionary or None if not found
        """
        job_data_raw = await self.redis.hgetall(f"{self.status_prefix}{job_id}")

        if not job_data_raw:
            return None

        # Convert to proper dict type
        job_data: dict[str, Any] = dict(job_data_raw)

        # Parse JSON array/object fields only
        # These fields are stored as JSON strings and need to be parsed
        json_fields = [
            "pii_findings",  # Array of PII findings
            "correction_results",  # Array of correction results per page
            "page_image_keys",  # Array of S3 keys for page images
            "verification_summary",  # Object with verification phase results
        ]

        for field in json_fields:
            if field in job_data and job_data[field]:
                try:
                    job_data[field] = json.loads(job_data[field])
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse {field} for job {job_id}")

        return job_data

    async def update_job_status(self, job_id: str, status: str, **additional_fields: Any) -> None:
        """
        Update job status and additional fields with automatic TTL adjustment.

        Atomically sets hash fields, TTL, and secondary index via Lua script.

        Args:
            job_id: Job identifier
            status: New status
            **additional_fields: Additional fields to update (auto-serialized if dict/list)
        """
        update_data: dict[str, str] = {
            "status": status,
            "updated_at": datetime.now(UTC).isoformat(),
        }

        # Serialize complex fields as JSON
        for field_key, value in additional_fields.items():
            if isinstance(value, (dict, list)):
                update_data[field_key] = json.dumps(value)
            else:
                update_data[field_key] = str(value)

        ttl = self._get_ttl_for_status(status)
        key = f"{self.status_prefix}{job_id}"
        now_ts = time.time()

        await self._script_hset_expire_zadd(
            keys=[key, self.jobs_index_key],
            args=[str(ttl), str(now_ts), job_id] + self._flatten_mapping(update_data),
        )

    async def add_pii_findings(self, job_id: str, findings: list[dict[str, Any]]) -> None:
        """
        Store PII scan results for a job.

        Updates job with PII findings without changing status or TTL.
        Keeps the secondary index timestamp fresh.

        Args:
            job_id: Job identifier
            findings: List of PII finding dictionaries
        """
        mapping = {
            "pii_findings": json.dumps(findings),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        key = f"{self.status_prefix}{job_id}"
        now_ts = time.time()

        await self._script_hset_zadd(
            keys=[key, self.jobs_index_key],
            args=[str(now_ts), job_id] + self._flatten_mapping(mapping),
        )

    async def add_processing_result(self, job_id: str, result_url: str, confidence: float) -> None:
        """
        Store processing completion data.

        Updates job with processing results without changing status or TTL.
        Keeps the secondary index timestamp fresh.

        Args:
            job_id: Job identifier
            result_url: URL to the processed result
            confidence: Processing confidence score (0.0 to 1.0)
        """
        now = datetime.now(UTC).isoformat()
        mapping = {
            "result_url": result_url,
            "confidence_score": str(confidence),
            "completed_at": now,
            "updated_at": now,
        }
        key = f"{self.status_prefix}{job_id}"
        now_ts = time.time()

        await self._script_hset_zadd(
            keys=[key, self.jobs_index_key],
            args=[str(now_ts), job_id] + self._flatten_mapping(mapping),
        )

    async def job_exists(self, job_id: str) -> bool:
        """
        Check if job exists in Redis.

        Args:
            job_id: Job identifier

        Returns:
            True if job exists, False otherwise
        """
        try:
            exists = await self.redis.exists(f"{self.status_prefix}{job_id}")
            return bool(exists)
        except Exception:
            return False

    async def delete_job(self, job_id: str) -> None:
        """
        Delete job hash and remove from secondary index atomically.

        Args:
            job_id: Job identifier
        """
        try:
            key = f"{self.status_prefix}{job_id}"
            await self._script_delete_job(
                keys=[key, self.jobs_index_key],
                args=[job_id],
            )
        except Exception as e:
            raise Exception(f"Failed to delete job {job_id}: {str(e)}")

    async def set_expiration(self, job_id: str, ttl_seconds: int) -> None:
        """
        Set TTL for automatic job cleanup.

        Args:
            job_id: Job identifier
            ttl_seconds: Time-to-live in seconds
        """
        try:
            await self.redis.expire(f"{self.status_prefix}{job_id}", ttl_seconds)
        except Exception as e:
            raise Exception(f"Failed to set expiration for job {job_id}: {str(e)}")

    async def list_all_jobs(self) -> list[str]:
        """List all job IDs in Redis.

        This method scans for all job keys matching the status prefix
        and extracts the job IDs. Used by orphan detection service.

        Returns:
            List of job IDs (without prefix)

        Example:
            >>> job_service = JobService(redis_client)
            >>> job_ids = await job_service.list_all_jobs()
            >>> print(f"Found {len(job_ids)} jobs")
        """
        try:
            # Use SCAN to find all job keys (safer than KEYS for production)
            pattern = f"{self.status_prefix}*"
            job_ids = []

            cursor = 0
            while True:
                cursor, keys = await self.redis.scan(cursor=cursor, match=pattern, count=100)

                # Extract job IDs from keys (remove prefix)
                for key in keys:
                    # Remove prefix to get job ID
                    job_id = key.replace(self.status_prefix, "")
                    job_ids.append(job_id)

                if cursor == 0:
                    break

            logger.debug(f"Found {len(job_ids)} jobs in Redis")
            return job_ids

        except Exception as e:
            logger.error(f"Error listing jobs: {str(e)}", exc_info=True)
            return []

    async def list_jobs_updated_before(self, cutoff_timestamp: float) -> list[str]:
        """List job IDs updated before the given Unix timestamp.

        Uses the ZRANGEBYSCORE on the jobs-by-updated sorted set for
        efficient lookup instead of scanning all keys.

        Args:
            cutoff_timestamp: Unix timestamp; jobs updated before this are returned.

        Returns:
            List of job IDs (without prefix)
        """
        try:
            members = await self.redis.zrangebyscore(
                self.jobs_index_key, "-inf", str(cutoff_timestamp)
            )
            return [m if isinstance(m, str) else m.decode("utf-8") for m in members]
        except Exception as e:
            logger.error(f"Error querying jobs-by-updated index: {e}", exc_info=True)
            return []

    async def backfill_jobs_index(self) -> int:
        """One-time migration: populate the jobs-by-updated sorted set from existing keys.

        Returns:
            Number of jobs added to the index.
        """
        job_ids = await self.list_all_jobs()
        added = 0
        for job_id in job_ids:
            try:
                job_data = await self.redis.hgetall(f"{self.status_prefix}{job_id}")
                if not job_data:
                    continue
                updated_at = job_data.get("updated_at") or job_data.get("created_at")
                if updated_at:
                    dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
                    ts = dt.timestamp()
                else:
                    ts = time.time()
                await self.redis.zadd(self.jobs_index_key, {job_id: ts})
                added += 1
            except Exception as e:
                logger.warning(f"Failed to backfill index for job {job_id}: {e}")
        logger.info(f"Backfilled jobs-by-updated index with {added} jobs")
        return added

    async def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Get job status and metadata (alias for get_job).

        This method is an alias for get_job() to match the naming
        convention used in other services (timeout_service, orphan_service).

        Args:
            job_id: Job identifier

        Returns:
            Job data dictionary or None if not found
        """
        return await self.get_job(job_id)

    async def cleanup_old_job(self, job_id: str) -> bool:
        """Delete old job hash and remove from secondary index atomically.

        Args:
            job_id: Job identifier (UUID)

        Returns:
            bool: True if job existed and was deleted, False if job didn't exist
        """
        try:
            key = f"{self.status_prefix}{job_id}"
            deleted_count = await self._script_delete_job(
                keys=[key, self.jobs_index_key],
                args=[job_id],
            )

            if deleted_count and int(deleted_count) > 0:
                logger.info(f"Cleaned up old job {job_id}")
                return True
            else:
                logger.debug(f"Job {job_id} does not exist (already cleaned up)")
                return False

        except Exception as e:
            logger.error(f"Error cleaning up job {job_id}: {str(e)}", exc_info=True)
            return False

    def _build_audit_record(self, job_data: dict[str, Any], reason: str) -> dict[str, Any]:
        """Build a structured audit record from job data.

        Includes summary information only – no actual PII content is logged.

        Args:
            job_data: Full job hash data from Redis
            reason: Why the job is being deleted/transitioned

        Returns:
            Structured dict suitable for JSON logging
        """
        # Summarize PII findings without including actual PII content
        pii_summary: dict[str, Any] | None = None
        pii_raw = job_data.get("pii_findings")
        if pii_raw:
            try:
                findings = json.loads(pii_raw) if isinstance(pii_raw, str) else pii_raw
                if isinstance(findings, list):
                    entity_types = list({f.get("entity_type", "unknown") for f in findings})
                    pii_summary = {"count": len(findings), "entity_types": entity_types}
            except (json.JSONDecodeError, TypeError):
                pii_summary = {"count": 0, "entity_types": [], "parse_error": True}

        return {
            "event": "job_audit",
            "job_id": job_data.get("job_id", "unknown"),
            "status": job_data.get("status", "unknown"),
            "created_at": job_data.get("created_at"),
            "updated_at": job_data.get("updated_at"),
            "completed_at": job_data.get("completed_at"),
            "confidence_score": job_data.get("confidence_score"),
            "error_message": job_data.get("error_message"),
            "pii_summary": pii_summary,
            "reason": reason,
        }

    async def emit_job_audit_log(self, job_id: str, reason: str) -> None:
        """Emit a structured JSON audit log for a job before deletion or forced transition.

        Never blocks the caller on failure – catches all exceptions and logs a warning.

        Args:
            job_id: Job identifier
            reason: Reason for the audit event (e.g., "retention_cleanup", "stuck_job_failed")
        """
        try:
            job_data = await self.redis.hgetall(f"{self.status_prefix}{job_id}")
            if not job_data:
                logger.warning(f"Cannot emit audit log for job {job_id}: job not found")
                return
            record = self._build_audit_record(dict(job_data), reason)
            logger.info(json.dumps(record))
        except Exception as e:
            logger.warning(f"Failed to emit audit log for job {job_id}: {e}")

    async def store_approval_token_mapping(self, approval_token: str, job_id: str, ttl_hours: int = 4) -> None:
        """Store approval token to job ID mapping for O(1) lookup.

        Creates a Redis key: eq-pdf:approval-token:{token} → job_id
        This enables direct token lookup without scanning all job hashes.

        Args:
            approval_token: Secure approval token
            job_id: Job identifier
            ttl_hours: Time-to-live in hours (matches approval expiration)

        Example:
            >>> job_service = JobService(redis_client)
            >>> await job_service.store_approval_token_mapping(
            ...     "abc123token",
            ...     "job-uuid-123",
            ...     ttl_hours=4
            ... )
        """
        try:
            token_key = f"eq-pdf:approval-token:{approval_token}"
            ttl_seconds = ttl_hours * 3600

            # Store mapping with expiration
            await self.redis.set(token_key, job_id, ex=ttl_seconds)

            logger.debug(f"Stored approval token mapping: {approval_token[:8]}... → {job_id}")

        except Exception as e:
            logger.error(f"Failed to store approval token mapping: {str(e)}", exc_info=True)
            raise Exception(f"Failed to store token mapping: {str(e)}")

    async def get_job_by_approval_token(self, token: str) -> dict[str, Any] | None:
        """Get job by approval token using O(1) Redis lookup.

        Uses token mapping stored in Redis to directly fetch job ID,
        then retrieves full job data. Much faster than scanning all jobs.

        Args:
            token: Approval token from URL

        Returns:
            Job data dictionary or None if token not found/expired

        Example:
            >>> job_service = JobService(redis_client)
            >>> job = await job_service.get_job_by_approval_token("abc123token")
            >>> if job:
            ...     print(f"Found job: {job['job_id']}")
        """
        try:
            # O(1) lookup in Redis
            token_key = f"eq-pdf:approval-token:{token}"
            job_id = await self.redis.get(token_key)

            if not job_id:
                logger.debug("Approval token not found or expired")
                return None

            # Decode if bytes
            if isinstance(job_id, bytes):
                job_id = job_id.decode("utf-8")

            # Fetch full job data
            return await self.get_job(job_id)

        except Exception as e:
            logger.error(f"Error fetching job by token: {str(e)}", exc_info=True)
            return None

    async def create_stream_token(self, job_id: str) -> str:
        """Generate single-use stream token for SSE authentication.

        Browser EventSource API cannot send custom headers, so this creates
        a short-lived token that can be passed as a query parameter.

        Token characteristics:
        - 256-bit entropy (cryptographically secure)
        - 5-minute TTL (connection should start immediately)
        - Single-use (consumed on first validation via GETDEL)
        - Job-scoped (validated against specific job_id)

        Args:
            job_id: Job identifier to associate with token

        Returns:
            Secure token string (43 characters, URL-safe)

        Example:
            >>> token = await job_service.create_stream_token("job-123")
            >>> # Client uses: EventSource(f"/stream?token={token}")
        """
        token = generate_secure_token()
        token_key = f"eq-pdf:stream-token:{token}"

        # Store token -> job_id mapping with short TTL (5 minutes)
        await self.redis.set(token_key, job_id, ex=300)

        logger.debug(f"Created stream token for job {job_id}: {token[:8]}...")
        return token

    async def validate_and_consume_stream_token(self, token: str) -> str | None:
        """Validate stream token and consume it atomically (single-use).

        Uses Redis GETDEL for atomic get-and-delete operation, ensuring
        the token can only be used once even under concurrent requests.

        Args:
            token: Stream token from query parameter

        Returns:
            job_id if token is valid, None if invalid/expired/already-consumed

        Example:
            >>> job_id = await job_service.validate_and_consume_stream_token("abc...")
            >>> if job_id:
            ...     # Token was valid and is now consumed
            ...     # Verify job_id matches requested job for security
        """
        token_key = f"eq-pdf:stream-token:{token}"

        # GETDEL: Atomic get-and-delete ensures single-use
        job_id = await self.redis.getdel(token_key)

        if not job_id:
            logger.debug("Stream token not found, expired, or already consumed")
            return None

        # Decode if bytes
        if isinstance(job_id, bytes):
            job_id = job_id.decode("utf-8")

        logger.debug(f"Consumed stream token for job {job_id}")
        return job_id
