"""Shared fixtures for integration tests.

Provides fixtures for:
- Real Redis and S3 clients from Docker environment
- Service instances with mocked AI components
- Test data generators
- Cleanup helpers
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService
from src.services.approval_service import ApprovalService
from src.workers.pii_worker import PIIWorker
from src.workers.processing_worker import ProcessingWorker
from src.shared.models.pii import PIIFinding
from src.config import settings


@pytest.fixture
def mock_redis_client():
    """Mock Redis client for isolated tests."""
    client = AsyncMock()
    # Mock common Redis operations
    client.lpush = AsyncMock()
    client.brpop = AsyncMock()
    client.llen = AsyncMock(return_value=0)
    client.hset = AsyncMock()
    client.hgetall = AsyncMock(return_value={})
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock()
    client.delete = AsyncMock()
    client.expire = AsyncMock()
    client.zadd = AsyncMock()
    client.zrem = AsyncMock()
    client.zrangebyscore = AsyncMock(return_value=[])
    client.ping = AsyncMock()
    client.exists = AsyncMock(return_value=0)
    client.scan = AsyncMock(return_value=(0, []))
    client.keys = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_s3_client():
    """Mock S3 client for isolated tests."""
    client = AsyncMock()
    # Add S3 exceptions
    client.exceptions = MagicMock()
    client.exceptions.NoSuchKey = type('NoSuchKey', (Exception,), {})
    # Mock common S3 operations
    client.put_object = AsyncMock()
    client.get_object = AsyncMock()
    client.delete_object = AsyncMock()
    client.list_objects_v2 = AsyncMock(return_value={'Contents': []})
    return client


@pytest.fixture
def storage_service(mock_s3_client):
    """Create StorageService with mocked S3."""
    return StorageService(
        s3_client=mock_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest.fixture
def queue_service(mock_redis_client):
    """Create QueueService with mocked Redis."""
    service = QueueService(redis_client=mock_redis_client)
    # Mock common methods
    service.enqueue = AsyncMock()
    service.dequeue = AsyncMock(return_value=None)
    service.queue_depth = AsyncMock(return_value=0)
    service.add_to_timeout_tracking = AsyncMock()
    service.remove_from_timeout_tracking = AsyncMock(return_value=True)
    return service


@pytest.fixture
def job_service(mock_redis_client):
    """Create JobService with stateful mocks that track job state."""
    service = JobService(redis_client=mock_redis_client)

    # In-memory job storage for tests
    jobs_db = {}

    async def create_job_mock(job_id: str, s3_key: str, status: str, **kwargs):
        jobs_db[job_id] = {
            "job_id": job_id,
            "s3_key": s3_key,
            "status": status,
            "created_at": datetime.now(timezone.utc).isoformat(),
            **kwargs
        }

    async def get_job_mock(job_id: str):
        return jobs_db.get(job_id)

    async def update_job_status_mock(job_id: str, status: str, **kwargs):
        if job_id in jobs_db:
            jobs_db[job_id]["status"] = status
            jobs_db[job_id].update(kwargs)

    async def job_exists_mock(job_id: str):
        return job_id in jobs_db

    async def delete_job_mock(job_id: str):
        jobs_db.pop(job_id, None)

    service.create_job = AsyncMock(side_effect=create_job_mock)
    service.get_job = AsyncMock(side_effect=get_job_mock)
    service.update_job_status = AsyncMock(side_effect=update_job_status_mock)
    service.job_exists = AsyncMock(side_effect=job_exists_mock)
    service.delete_job = AsyncMock(side_effect=delete_job_mock)

    return service


@pytest.fixture
def approval_service(mock_redis_client, mock_s3_client, job_service, queue_service):
    """Create ApprovalService with mocked dependencies."""
    return ApprovalService(
        redis_client=mock_redis_client,
        s3_client=mock_s3_client,
        job_service=job_service,
        queue_service=queue_service
    )


@pytest.fixture
def mock_pii_analyzer():
    """Mock PII analyzer to avoid external dependencies."""
    with patch('src.services.pii_analyzer.get_pii_analyzer') as mock:
        analyzer = MagicMock()
        # Default: no PII detected
        analyzer.analyze_text.return_value = []
        mock.return_value = analyzer
        yield analyzer


@pytest.fixture
def mock_pdf_extractor():
    """Mock PDF text extractor."""
    with patch('src.services.pii_service.extract_pdf_text') as mock:
        # Default: return simple text
        mock.return_value = "Sample PDF text content for testing."
        yield mock


@pytest.fixture
def mock_pdf_converter():
    """Mock PDF converter for processing worker tests."""
    with patch('src.services.pdf_converter.PDFConverter') as mock:
        converter = MagicMock()
        # Mock conversion result
        conversion_result = MagicMock()
        conversion_result.has_page_images = True
        conversion_result.total_pages = 1
        conversion_result.full_markdown = "# Sample Document\n\nTest content."
        conversion_result.pages = [MagicMock(page_num=1)]
        converter.convert_with_page_images = AsyncMock(return_value=conversion_result)
        mock.return_value = converter
        yield converter


@pytest.fixture
def mock_ai_enhancement():
    """Mock AI enhancement service for processing worker tests."""
    with patch('src.services.ai_enhancement_service.AIEnhancementService') as mock:
        ai_service = MagicMock()
        # Mock page processing result
        improvement_result = MagicMock()
        improvement_result.confidence_score = 0.95
        ai_service.process_pages_concurrently = AsyncMock(return_value=[improvement_result])
        ai_service.combine_page_markdown = MagicMock(
            return_value="# Sample Document\n\nEnhanced content with accessibility improvements."
        )
        mock.return_value = ai_service
        yield ai_service


@pytest.fixture
def pii_worker(storage_service, queue_service, job_service):
    """Create PIIWorker instance with mocked services."""
    return PIIWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )


@pytest.fixture
def processing_worker(storage_service, queue_service, job_service):
    """Create ProcessingWorker instance with mocked services."""
    return ProcessingWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )


@pytest.fixture
def sample_job_id():
    """Generate a valid UUID job ID."""
    return str(uuid.uuid4())


@pytest.fixture
def sample_s3_key(sample_job_id):
    """Generate a sample S3 key."""
    return f"temp/{sample_job_id}/test.pdf"


@pytest.fixture
def sample_pdf_content():
    """Generate sample PDF binary content."""
    # Minimal valid PDF structure
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\nxref\n0 1\ntrailer\n<< /Size 1 /Root 1 0 R >>\nstartxref\n100\n%%EOF"


@pytest.fixture
def sample_pii_findings():
    """Generate sample PII findings."""
    return [
        PIIFinding(
            entity_type="PERSON",
            text="John Doe",
            score=0.95,
            start=10,
            end=18
        ),
        PIIFinding(
            entity_type="EMAIL_ADDRESS",
            text="john@example.com",
            score=0.99,
            start=100,
            end=116
        )
    ]


@pytest.fixture
async def cleanup_redis(mock_redis_client):
    """Cleanup Redis state after tests."""
    yield
    # Reset all mock call counts
    mock_redis_client.reset_mock()


@pytest.fixture
async def cleanup_s3(mock_s3_client):
    """Cleanup S3 state after tests."""
    yield
    # Reset all mock call counts
    mock_s3_client.reset_mock()
