# PRD-008: Timeout & Cleanup Service

## Overview
**Epic**: MVP PDF Converter Background Maintenance Service
**Phase**: 2 - Core Modules
**Estimated Effort**: 2 days
**Dependencies**:
- PRD-001 (Infrastructure)
- PRD-002 (Data Models)
- **PRD-003 (Shared Services) - REQUIRED** - Must be complete before starting this worker

## Problem Statement
The monolith application requires a **background scheduler thread** that monitors approval timeouts, cleans up expired jobs and temporary files, and maintains system health through automated cleanup operations. This worker runs as part of the same Python process as the FastAPI API and imports shared services built in PRD-003.

**Architecture Note:** This is a **background scheduler thread** running within the monolith application, not an independent module. It shares storage_service, queue_service, and job_service with the API and other workers. All Redis connections and S3 clients are shared across the application.

## Success Criteria
- [ ] Approval timeout monitoring using Redis sorted sets
- [ ] Automatic job failure for expired approvals
- [ ] Uses shared services from PRD-003 (storage_service, queue_service, job_service)
- [ ] S3 temporary file cleanup operations
- [ ] Orphaned data detection and removal
- [ ] System metrics updates for cleanup operations
- [ ] Configurable cleanup schedules and retention policies

## Shared Service Dependencies
This worker imports and uses the following shared services built in PRD-003:

- **storage_service.cleanup_temp_files_for_job()** - Cleans up S3 temp files for specific jobs
- **storage_service.list_temp_files()** - Lists temp files for age-based cleanup
- **storage_service.delete_from_s3()** - Deletes expired temp files from S3
- **queue_service.get_approval_timeouts()** - Monitors Redis sorted set for expired approvals
- **queue_service.remove_from_timeout_tracking()** - Removes processed timeouts
- **job_service.get_job_status()** - Retrieves job status for timeout processing
- **job_service.update_job_status()** - Updates job status to "failed" for timeouts
- **job_service.cleanup_old_job()** - Removes old job records from Redis

These services MUST be implemented in PRD-003 before this worker can be developed.

## Technical Requirements

### Timeout Monitoring System

#### Approval Timeout Management
```python
async def monitor_approval_timeouts():
    """Monitor and process expired approval timeouts"""
    try:
        current_time = datetime.utcnow().timestamp()

        # Get expired approvals from sorted set
        expired_jobs = await redis.zrangebyscore(
            APPROVAL_TIMEOUTS,
            0, current_time,
            withscores=True
        )

        for job_id, expired_timestamp in expired_jobs:
            await process_expired_approval(
                job_id.decode('utf-8'),
                datetime.fromtimestamp(expired_timestamp)
            )

            # Remove from timeout tracking
            await redis.zrem(APPROVAL_TIMEOUTS, job_id)

    except Exception as e:
        logger.error(f"Timeout monitoring error: {e}")
        await update_cleanup_metrics("timeout_monitor_errors", 1)

async def process_expired_approval(job_id: str, expired_at: datetime):
    """Process a single expired approval"""
    try:
        # Get current job status
        job_status = await get_job_status(job_id)

        if job_status and job_status.status == "awaiting_approval":
            logger.info(f"Processing expired approval for job {job_id}")

            # Update job status to failed
            await update_job_status(
                job_id,
                "failed",
                error_message=f"Approval timeout - expired at {expired_at.isoformat()}"
            )

            # Clean up associated temp files
            await cleanup_temp_files_for_job(job_id)

            # Update metrics
            await update_cleanup_metrics("approval_timeouts", 1)

        else:
            logger.warning(f"Job {job_id} not in awaiting_approval status during timeout")

    except Exception as e:
        logger.error(f"Failed to process expired approval {job_id}: {e}")
```

### S3 Cleanup Operations

#### Temporary File Cleanup
```python
async def cleanup_temp_files():
    """Clean up expired temporary files from S3"""
    try:
        cutoff_time = datetime.utcnow() - timedelta(days=TEMP_FILE_RETENTION_DAYS)

        # List objects in temp bucket older than cutoff
        paginator = s3_client.get_paginator('list_objects_v2')

        cleanup_count = 0
        total_size = 0

        async for page in paginator.paginate(Bucket=S3_TEMP_BUCKET):
            if 'Contents' not in page:
                continue

            for obj in page['Contents']:
                if obj['LastModified'].replace(tzinfo=None) < cutoff_time:
                    try:
                        # Delete the object
                        await s3_client.delete_object(
                            Bucket=S3_TEMP_BUCKET,
                            Key=obj['Key']
                        )

                        cleanup_count += 1
                        total_size += obj['Size']

                        logger.debug(f"Deleted temp file: {obj['Key']}")

                    except Exception as e:
                        logger.error(f"Failed to delete {obj['Key']}: {e}")

        # Update cleanup metrics
        await update_cleanup_metrics("temp_files_deleted", cleanup_count)
        await update_cleanup_metrics("temp_storage_freed_bytes", total_size)

        logger.info(f"Cleaned up {cleanup_count} temp files ({total_size} bytes)")

    except Exception as e:
        logger.error(f"Temp file cleanup error: {e}")
        await update_cleanup_metrics("temp_cleanup_errors", 1)

async def cleanup_temp_files_for_job(job_id: str):
    """Clean up temporary files for a specific job"""
    try:
        # List all objects with job_id prefix
        response = await s3_client.list_objects_v2(
            Bucket=S3_TEMP_BUCKET,
            Prefix=f"temp/{job_id}/"
        )

        if 'Contents' in response:
            # Delete all objects for this job
            objects_to_delete = [{'Key': obj['Key']} for obj in response['Contents']]

            await s3_client.delete_objects(
                Bucket=S3_TEMP_BUCKET,
                Delete={'Objects': objects_to_delete}
            )

            logger.info(f"Cleaned up {len(objects_to_delete)} temp files for job {job_id}")

    except Exception as e:
        logger.error(f"Failed to cleanup temp files for job {job_id}: {e}")
```

### Orphaned Data Detection

#### Job Status Cleanup
```python
async def cleanup_orphaned_job_data():
    """Clean up orphaned job data and inconsistent states"""
    try:
        # Get all job status keys
        job_keys = await redis.keys(f"{JOB_STATUS_PREFIX}*")

        orphaned_count = 0

        for job_key in job_keys:
            try:
                job_id = job_key.decode('utf-8').split(':')[-1]
                job_status = await get_job_status(job_id)

                if not job_status:
                    continue

                # Check for jobs older than max retention
                job_age = datetime.utcnow() - job_status.created_at

                if job_age > timedelta(days=JOB_RETENTION_DAYS):
                    # Clean up old completed/failed jobs
                    if job_status.status in ["completed", "failed", "denied"]:
                        await cleanup_old_job(job_id, job_status)
                        orphaned_count += 1

                # Check for stuck jobs
                elif job_status.status == "processing":
                    processing_age = datetime.utcnow() - job_status.updated_at

                    if processing_age > timedelta(hours=MAX_PROCESSING_HOURS):
                        logger.warning(f"Found stuck processing job: {job_id}")
                        await update_job_status(
                            job_id,
                            "failed",
                            error_message=f"Processing timeout after {processing_age}"
                        )
                        await cleanup_temp_files_for_job(job_id)

            except Exception as e:
                logger.error(f"Failed to process job {job_key}: {e}")

        await update_cleanup_metrics("orphaned_jobs_cleaned", orphaned_count)

    except Exception as e:
        logger.error(f"Orphaned data cleanup error: {e}")
        await update_cleanup_metrics("orphan_cleanup_errors", 1)

async def cleanup_old_job(job_id: str, job_status: JobStatus):
    """Clean up an old completed job"""
    try:
        # Remove job status from Redis
        await redis.delete(f"{JOB_STATUS_PREFIX}{job_id}")

        # Clean up temp files
        await cleanup_temp_files_for_job(job_id)

        # Note: Keep results in S3 for longer retention
        # Results cleanup is handled separately with different retention policy

        logger.info(f"Cleaned up old job: {job_id} (status: {job_status.status})")

    except Exception as e:
        logger.error(f"Failed to cleanup old job {job_id}: {e}")
```

### Worker Main Loop

#### Cleanup Scheduler
```python
async def cleanup_worker_main():
    """Main cleanup worker with scheduled tasks"""
    logger.info("Cleanup worker starting")

    # Task scheduling
    last_approval_check = datetime.min
    last_temp_cleanup = datetime.min
    last_orphan_cleanup = datetime.min
    last_metrics_cleanup = datetime.min

    while True:
        try:
            current_time = datetime.utcnow()

            # Approval timeouts - check every 30 seconds
            if current_time - last_approval_check > timedelta(seconds=30):
                await monitor_approval_timeouts()
                last_approval_check = current_time

            # Temp file cleanup - every 1 hour
            if current_time - last_temp_cleanup > timedelta(hours=1):
                await cleanup_temp_files()
                last_temp_cleanup = current_time

            # Orphaned data cleanup - every 4 hours
            if current_time - last_orphan_cleanup > timedelta(hours=4):
                await cleanup_orphaned_job_data()
                last_orphan_cleanup = current_time

            # Metrics cleanup - daily
            if current_time - last_metrics_cleanup > timedelta(days=1):
                await cleanup_old_metrics()
                last_metrics_cleanup = current_time

            # Sleep between iterations
            await asyncio.sleep(10)

        except Exception as e:
            logger.error(f"Cleanup worker error: {e}")
            await asyncio.sleep(30)  # Longer pause on errors

async def cleanup_old_metrics():
    """Clean up old metrics data"""
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=METRICS_RETENTION_DAYS)

        # Clean up daily metrics older than retention period
        old_metric_keys = await redis.keys(f"{DAILY_METRICS}:*")

        deleted_count = 0
        for key in old_metric_keys:
            try:
                key_str = key.decode('utf-8')
                date_str = key_str.split(':')[-1]
                metric_date = datetime.strptime(date_str, '%Y%m%d')

                if metric_date < cutoff_date:
                    await redis.delete(key)
                    deleted_count += 1

            except Exception as e:
                logger.error(f"Failed to parse metric key {key}: {e}")

        logger.info(f"Cleaned up {deleted_count} old metric entries")

    except Exception as e:
        logger.error(f"Metrics cleanup error: {e}")
```

### Metrics Management

#### Cleanup Metrics Tracking
```python
async def update_cleanup_metrics(metric_name: str, value: int):
    """Update cleanup operation metrics"""
    try:
        date_key = datetime.utcnow().strftime('%Y%m%d')
        metric_key = f"{DAILY_METRICS}:{date_key}"

        await redis.hincrby(metric_key, f"cleanup_{metric_name}", value)

        # Set expiration for metrics (longer than retention for safety)
        await redis.expire(metric_key, METRICS_RETENTION_DAYS * 24 * 60 * 60 + 86400)

    except Exception as e:
        logger.error(f"Failed to update cleanup metrics: {e}")

async def get_cleanup_health_status() -> CleanupHealthStatus:
    """Get current cleanup system health status"""
    try:
        current_date = datetime.utcnow().strftime('%Y%m%d')
        today_metrics = await redis.hgetall(f"{DAILY_METRICS}:{current_date}")

        return CleanupHealthStatus(
            last_approval_timeout_check=datetime.utcnow(),  # Would track in Redis
            approval_timeouts_processed_today=int(today_metrics.get(b'cleanup_approval_timeouts', 0)),
            temp_files_deleted_today=int(today_metrics.get(b'cleanup_temp_files_deleted', 0)),
            temp_storage_freed_bytes_today=int(today_metrics.get(b'cleanup_temp_storage_freed_bytes', 0)),
            orphaned_jobs_cleaned_today=int(today_metrics.get(b'cleanup_orphaned_jobs_cleaned', 0)),
            errors_today=int(today_metrics.get(b'cleanup_timeout_monitor_errors', 0)) +
                        int(today_metrics.get(b'cleanup_temp_cleanup_errors', 0)) +
                        int(today_metrics.get(b'cleanup_orphan_cleanup_errors', 0))
        )

    except Exception as e:
        logger.error(f"Failed to get cleanup health status: {e}")
        return CleanupHealthStatus()
```

## Acceptance Criteria

### 1. Timeout Monitoring
- [ ] Redis sorted set monitoring for approval timeouts
- [ ] Automatic processing of expired approvals
- [ ] Proper job status updates to "failed"
- [ ] Cleanup of associated temporary files
- [ ] Configurable timeout thresholds

### 2. S3 Cleanup Operations
- [ ] Scheduled temp file cleanup based on age
- [ ] Job-specific temp file cleanup on demand
- [ ] Size and count metrics for cleanup operations
- [ ] Error handling for S3 operation failures
- [ ] Configurable retention policies

### 3. Orphaned Data Detection
- [ ] Detection of stuck processing jobs
- [ ] Cleanup of old completed/failed jobs
- [ ] Removal of inconsistent job states
- [ ] Configurable job retention periods
- [ ] Metrics tracking for cleanup operations

### 4. Scheduling and Performance
- [ ] Efficient task scheduling with appropriate intervals
- [ ] Non-blocking operation that doesn't impact other services
- [ ] Resource usage monitoring and optimization
- [ ] Graceful error handling and recovery
- [ ] Worker restarts automatically on failure

### 5. Metrics and Monitoring
- [ ] Daily metrics tracking for all cleanup operations
- [ ] Health status reporting for monitoring
- [ ] Error count and success rate tracking
- [ ] Storage savings and performance metrics
- [ ] Integration with system monitoring

## Deliverables

### Files to Create
```
/src/services/
├── cleanup_service.py                # Main cleanup service module
├── timeout_service.py                # Approval timeout monitoring
├── s3_cleanup_service.py             # S3 cleanup operations
├── orphan_service.py                 # Orphaned data detection
├── metrics_service.py                # Metrics management
├── cleanup_worker.py                 # Worker main loop

/src/utils/
├── date_utils.py                     # Date/time utilities
└── scheduler_utils.py                # Task scheduling utilities

/tests/services/
├── test_timeout_monitoring.py        # Timeout logic tests
├── test_s3_cleanup.py                # S3 cleanup tests
├── test_orphan_detection.py          # Orphan detection tests
└── test_scheduling.py                # Scheduler tests

/config/
└── cleanup_config.yaml               # Cleanup configuration
```

### Worker Execution
The cleanup worker runs as a **background asyncio task** started by src/main.py:

```python
# src/main.py
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from .workers.timeout_worker import timeout_worker_main

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start timeout/cleanup worker as background task
    asyncio.create_task(timeout_worker_main())
    asyncio.create_task(pii_worker_main())
    asyncio.create_task(processing_worker_main())
    yield

app = FastAPI(lifespan=lifespan)
```

```bash
# Start infrastructure services
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Run the monolith application (starts API + all workers)
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8080

# src/main.py automatically starts:
# - FastAPI server (main thread)
# - PII worker (background task)
# - Processing worker (background task)
# - Timeout/cleanup scheduler (background task) ← This worker
```

**Architecture Note:** The worker shares Redis connections and S3 clients with all other parts of the application. All cleanup operations use the same storage_service and job_service instances.

## Technical Notes

### Configuration Management
```python
# Cleanup configuration
class CleanupConfig:
    # Approval timeouts
    APPROVAL_TIMEOUT_CHECK_SECONDS = 30

    # File retention policies
    TEMP_FILE_RETENTION_DAYS = 1
    JOB_RETENTION_DAYS = 30
    METRICS_RETENTION_DAYS = 90

    # Processing timeouts
    MAX_PROCESSING_HOURS = 2

    # Cleanup schedules
    TEMP_CLEANUP_INTERVAL_HOURS = 1
    ORPHAN_CLEANUP_INTERVAL_HOURS = 4
    METRICS_CLEANUP_INTERVAL_HOURS = 24

    # Performance settings
    S3_BATCH_SIZE = 1000
    REDIS_SCAN_COUNT = 100

    # Error handling
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 5

# Health status model
class CleanupHealthStatus(BaseModel):
    last_approval_timeout_check: datetime
    approval_timeouts_processed_today: int
    temp_files_deleted_today: int
    temp_storage_freed_bytes_today: int
    orphaned_jobs_cleaned_today: int
    errors_today: int

    @property
    def is_healthy(self) -> bool:
        """Determine if cleanup system is healthy"""
        # Check if last timeout check was recent
        if datetime.utcnow() - self.last_approval_timeout_check > timedelta(minutes=2):
            return False

        # Check error rate
        total_operations = (self.approval_timeouts_processed_today +
                          self.temp_files_deleted_today +
                          self.orphaned_jobs_cleaned_today)

        if total_operations > 0 and self.errors_today / total_operations > 0.1:
            return False

        return True
```

### Redis Operations Optimization
```python
# Efficient Redis operations for large-scale cleanup
async def batch_process_timeouts(batch_size: int = 100):
    """Process timeouts in batches for better performance"""
    current_time = datetime.utcnow().timestamp()

    while True:
        # Get a batch of expired jobs
        expired_batch = await redis.zrangebyscore(
            APPROVAL_TIMEOUTS,
            0, current_time,
            start=0, num=batch_size,
            withscores=True
        )

        if not expired_batch:
            break

        # Process batch concurrently
        tasks = [
            process_expired_approval(job_id.decode('utf-8'),
                                   datetime.fromtimestamp(score))
            for job_id, score in expired_batch
        ]

        await asyncio.gather(*tasks, return_exceptions=True)

        # Remove processed items
        job_ids = [job_id for job_id, _ in expired_batch]
        if job_ids:
            await redis.zrem(APPROVAL_TIMEOUTS, *job_ids)
```

### Environment Configuration
```python
# Environment variables required
REDIS_URL=redis://redis:6379
AWS_ENDPOINT_URL=http://localstack:4566
S3_TEMP_BUCKET=equalify-temp
S3_RESULTS_BUCKET=equalify-results

# Cleanup schedules (in seconds)
APPROVAL_TIMEOUT_CHECK_INTERVAL=30
TEMP_CLEANUP_INTERVAL=3600
ORPHAN_CLEANUP_INTERVAL=14400
METRICS_CLEANUP_INTERVAL=86400

# Retention policies (in days)
TEMP_FILE_RETENTION_DAYS=1
JOB_RETENTION_DAYS=30
METRICS_RETENTION_DAYS=90

# Performance settings
MAX_PROCESSING_HOURS=2
S3_CLEANUP_BATCH_SIZE=1000
CONCURRENT_CLEANUP_TASKS=5

# Health check
HEALTH_CHECK_PORT=8080
```

## Definition of Done
- [ ] Approval timeout monitoring operational
- [ ] S3 temp file cleanup working correctly
- [ ] Orphaned data detection and cleanup implemented
- [ ] Scheduled task execution working reliably
- [ ] Metrics tracking and health status reporting
- [ ] Module integrates with main application
- [ ] Integration tests with Redis and S3 pass
- [ ] Performance meets cleanup efficiency targets
- [ ] Error handling covers all edge cases
- [ ] Documentation complete and accurate
- [ ] Module ready for continuous background operation