"""Shared fixtures for integration tests.

Provides fixtures for:
- Real Redis and S3 via testcontainers (true isolation)
- Service instances with real infrastructure (mocked AI only)
- Test data generators
- Cleanup helpers
"""

import os
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import boto3
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from src.config import settings
from src.services.approval_service import ApprovalService
from src.services.job_service import JobService
from src.services.queue_service import QueueService
from src.services.s3_url_service import S3URLService
from src.services.storage_service import StorageService
from src.shared.models.pii import PIIFinding
from src.workers.pii_worker import PIIWorker
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.redis import RedisContainer

# ============================================================================
# TEST CONFIGURATION - Disable Background Workers
# ============================================================================


@pytest.fixture(scope="session", autouse=True)
def disable_background_workers():
    """Disable background workers for all integration tests.

    Integration tests use real Redis/S3 services but should NOT have live
    workers consuming from queues. This fixture sets DISABLE_WORKERS=true
    before any tests run.
    """
    os.environ["DISABLE_WORKERS"] = "true"
    yield
    # Cleanup: remove the environment variable after tests
    os.environ.pop("DISABLE_WORKERS", None)


# ============================================================================
# TESTCONTAINER FIXTURES - Isolated Infrastructure
# ============================================================================


@pytest.fixture(scope="session")
def redis_container():
    """Session-scoped Redis container via testcontainers.

    Container starts once per test session, providing isolated Redis instance.
    Testcontainers automatically assigns random port to avoid conflicts.
    """
    with RedisContainer("redis:7-alpine") as redis:
        yield redis


@pytest.fixture(scope="session")
def floci_container():
    """Session-scoped Floci container via testcontainers.

    Floci is a lightweight AWS emulator (replaces LocalStack). Uses the
    generic DockerContainer helper since testcontainers has no first-class
    Floci module; we wait for the "Emulator Ready" banner instead of a
    health endpoint because Floci doesn't expose /_localstack/health.
    """
    container = (
        DockerContainer("hectorvent/floci:1.5.3")
        .with_exposed_ports(4566)
        .with_env("FLOCI_DEFAULT_REGION", "us-east-1")
    )
    with container:
        wait_for_logs(container, "=== AWS Local Emulator Ready ===", timeout=30)
        yield container


# ============================================================================
# CLIENT FIXTURES - Fresh Clients Per Test
# ============================================================================


@pytest_asyncio.fixture
async def real_redis_client(redis_container) -> AsyncGenerator[aioredis.Redis, None]:
    """Real Redis client connected to testcontainer with per-test cleanup.

    Each test gets a fresh Redis database with automatic cleanup before and after.
    No shared state between tests - true isolation.
    """
    # Build Redis connection URL from testcontainer
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    connection_url = f"redis://{host}:{port}"

    client = await aioredis.from_url(connection_url, decode_responses=True)

    # Cleanup before test (fresh start)
    await client.flushdb()

    yield client

    # Cleanup after test (prevent state leakage)
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def real_s3_client(floci_container):
    """Real S3 client connected to Floci testcontainer with per-test cleanup.

    Each test gets a fresh S3 environment with buckets pre-created.
    Testcontainers handles container lifecycle and cleanup.
    """
    # Build Floci endpoint URL from testcontainer
    host = floci_container.get_container_host_ip()
    port = floci_container.get_exposed_port(4566)
    endpoint_url = f"http://{host}:{port}"

    # Create S3 client (no AWS_PROFILE issues with testcontainers)
    s3_client = boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        region_name="us-east-1",
    )

    # Create test buckets
    try:
        s3_client.create_bucket(Bucket=settings.s3_temp_bucket)
        s3_client.create_bucket(Bucket=settings.s3_results_bucket)
    except Exception:
        pass  # Buckets may already exist

    yield s3_client

    # Cleanup: delete all objects in test buckets
    for bucket in [settings.s3_temp_bucket, settings.s3_results_bucket]:
        try:
            response = s3_client.list_objects_v2(Bucket=bucket)
            if "Contents" in response:
                for obj in response["Contents"]:
                    s3_client.delete_object(Bucket=bucket, Key=obj["Key"])
        except Exception:
            pass  # Bucket may not exist


# ============================================================================
# REAL SERVICE FIXTURES - Using Real Infrastructure
# ============================================================================


@pytest_asyncio.fixture
async def storage_service(real_s3_client):
    """Create StorageService with REAL S3 (testcontainer Floci)."""
    return StorageService(
        s3_client=real_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest_asyncio.fixture
async def queue_service(real_redis_client):
    """Create QueueService with REAL Redis (testcontainer)."""
    return QueueService(redis_client=real_redis_client)


@pytest_asyncio.fixture
async def job_service(real_redis_client):
    """Create JobService with REAL Redis (testcontainer)."""
    return JobService(redis_client=real_redis_client)


@pytest_asyncio.fixture
async def s3_url_service(real_s3_client):
    """Create S3URLService with REAL S3 (testcontainer Floci)."""
    return S3URLService(
        s3_client=real_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest_asyncio.fixture
async def approval_service(
    real_redis_client, real_s3_client, job_service, queue_service, storage_service, s3_url_service
):
    """Create ApprovalService with real dependencies.

    NOTE: DocumentProcessingService is mocked in the fixture to prevent actual
    document processing during approval race condition tests.
    """
    # Patch where DocumentProcessingService is defined (it's late-imported in approval_service)
    with patch("src.services.document_processing_service.DocumentProcessingService") as mock_processing_class:
        # Mock the processing service to prevent actual processing
        mock_processing_service = MagicMock()
        mock_processing_service.process_document = AsyncMock()
        mock_processing_class.return_value = mock_processing_service

        yield ApprovalService(
            redis_client=real_redis_client,
            s3_client=real_s3_client,
            job_service=job_service,
            queue_service=queue_service,
            storage_service=storage_service,
            s3_url_service=s3_url_service,
        )


# ============================================================================
# MOCKED AI/ML FIXTURES - Expensive Components (Keep Mocked)
# ============================================================================


@pytest.fixture(autouse=True)
def mock_pipeline_processing(request):
    """Auto-mock PipelineViewerService for integration tests (no API keys needed).

    Mocks the pipeline processing to avoid real LLM calls during integration tests.
    """
    from unittest.mock import AsyncMock, patch

    from src.services.pipeline_viewer_models import PipelineViewerResult

    # Skip mocking for bedrock integration tests (they test real Bedrock)
    if "test_bedrock_agent" in request.node.nodeid:
        yield
        return

    mock_result = PipelineViewerResult(
        filename="test.pdf",
        versions={"v0": "# Test Document\n\nMock content."},
        steps=[],
        figures=[],
        total_pages=1,
    )

    with patch(
        "src.services.pipeline_viewer.PipelineViewerService.process",
        new_callable=AsyncMock,
        return_value=mock_result,
    ):
        yield


@pytest.fixture
def mock_pii_analyzer():
    """Mock PII analyzer to avoid external dependencies."""
    with patch("src.services.pii_analyzer.get_pii_analyzer") as mock:
        analyzer = MagicMock()
        # Default: no PII detected
        analyzer.analyze_text.return_value = []
        mock.return_value = analyzer
        yield analyzer


@pytest.fixture
def mock_pdf_extractor():
    """Mock PDF text extractor to avoid Docling model downloads in tests.

    Uses AsyncMock since extract_pdf_text is an async function.
    This prevents CI timeout issues from Docling downloading models at runtime.
    """
    with patch("src.services.pii_service.extract_pdf_text", new_callable=AsyncMock) as mock:
        # Default: return simple text
        mock.return_value = "Sample PDF text content for testing."
        yield mock


@pytest.fixture
def mock_pdf_converter():
    """Mock PDF converter for processing worker tests."""
    converter = MagicMock()
    # Mock conversion result
    conversion_result = MagicMock()
    conversion_result.has_page_images = True
    conversion_result.total_pages = 1
    conversion_result.full_markdown = "# Sample Document\n\nTest content."
    conversion_result.pages = [MagicMock(page_num=1)]
    converter.convert_with_page_images = AsyncMock(return_value=conversion_result)
    return converter


@pytest.fixture
def mock_ai_enhancement():
    """Mock AI enhancement service for processing worker tests."""
    ai_service = MagicMock()
    # Mock page processing result
    improvement_result = MagicMock()
    improvement_result.confidence_score = 0.95
    ai_service.process_pages_concurrently = AsyncMock(return_value=[improvement_result])
    ai_service.combine_page_markdown = MagicMock(
        return_value="# Sample Document\n\nEnhanced content with accessibility improvements."
    )
    return ai_service


# ============================================================================
# WORKER FIXTURES - Using Real Services
# ============================================================================


@pytest_asyncio.fixture
async def pii_worker(storage_service, queue_service, job_service, mock_pii_analyzer, mock_pdf_extractor):
    """Create PIIWorker instance with REAL services and MOCKED PII analyzer/PDF extractor.

    The mock_pdf_extractor patches extract_pdf_text to avoid Docling model downloads
    during tests (which would cause timeouts in CI).
    """
    from src.services.pii_service import PIIDetectionService

    # Create PIIDetectionService with mocked PII analyzer
    # Note: mock_pdf_extractor is a context manager fixture that patches
    # src.services.pii_service.extract_pdf_text automatically
    pii_service = PIIDetectionService(
        storage_service=storage_service, queue_service=queue_service, job_service=job_service
    )
    # Replace the auto-created analyzer with our mocked one
    pii_service.pii_analyzer = mock_pii_analyzer

    # Create worker and inject the pre-configured pii service
    worker = PIIWorker(storage_service=storage_service, queue_service=queue_service, job_service=job_service)
    # Replace the auto-created pii_service with our mocked one
    worker.pii_service = pii_service

    return worker


# OLD ProcessingService fixture removed - system now uses DocumentProcessingService
# with agentic pipeline. Integration tests that need processing service should
# create their own fixtures using DocumentProcessingService.


# ============================================================================
# TEST DATA FIXTURES
# ============================================================================


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
    """Generate valid PDF binary content using reportlab.

    Creates a simple single-page PDF that Docling can actually process.
    """
    from io import BytesIO

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)

    # Add some simple text content
    pdf.drawString(100, 750, "Sample PDF Document")
    pdf.drawString(100, 730, "This is a test document for integration testing.")
    pdf.drawString(100, 710, "It contains basic text content that can be extracted.")

    pdf.showPage()
    pdf.save()

    return buffer.getvalue()


@pytest.fixture
def sample_pii_findings():
    """Generate sample PII findings."""
    return [
        PIIFinding(entity_type="PERSON", text="John Doe", score=0.95, start=10, end=18),
        PIIFinding(entity_type="EMAIL_ADDRESS", text="john@example.com", score=0.99, start=100, end=116),
    ]
