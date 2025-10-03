"""Shared fixtures for integration tests.

Provides fixtures for:
- Real Redis and S3 clients via testcontainers
- Service instances with real infrastructure (mocked AI only)
- Test data generators
- Cleanup helpers
"""

import pytest
import uuid
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import AsyncGenerator
import redis.asyncio as aioredis
from testcontainers.redis import RedisContainer
from testcontainers.localstack import LocalStackContainer
import boto3

from src.services.storage_service import StorageService
from src.services.queue_service import QueueService
from src.services.job_service import JobService
from src.services.approval_service import ApprovalService
from src.workers.pii_worker import PIIWorker
from src.workers.processing_worker import ProcessingWorker
from src.shared.models.pii import PIIFinding
from src.config import settings


# ============================================================================
# TESTCONTAINER FIXTURES - Real Infrastructure
# ============================================================================

@pytest.fixture(scope="session")
def redis_container():
    """Start Redis container for integration tests (session-scoped)."""
    container = RedisContainer("redis:7-alpine")
    container.start()
    yield container
    container.stop()


@pytest.fixture(scope="session")
def localstack_container():
    """Start LocalStack container for S3 integration tests (session-scoped)."""
    container = LocalStackContainer(image="localstack/localstack:latest")
    container.with_services("s3")
    container.start()
    yield container
    container.stop()


@pytest.fixture
async def real_redis_client(redis_container) -> AsyncGenerator[aioredis.Redis, None]:
    """Real Redis client connected to testcontainer with per-test cleanup."""
    # Get connection details from container
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    redis_url = f"redis://{host}:{port}/0"

    client = await aioredis.from_url(redis_url, decode_responses=True)

    yield client

    # Cleanup: flush all data after each test
    await client.flushall()
    await client.aclose()


@pytest.fixture
def real_s3_client(localstack_container):
    """Real S3 client connected to LocalStack testcontainer with per-test cleanup."""
    # Get LocalStack endpoint
    host = localstack_container.get_container_host_ip()
    port = localstack_container.get_exposed_port(4566)
    endpoint_url = f"http://{host}:{port}"

    # Use SYNC boto3 client (not async) - matches existing StorageService
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

@pytest.fixture
async def storage_service(real_s3_client):
    """Create StorageService with REAL S3 (LocalStack)."""
    return StorageService(
        s3_client=real_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest.fixture
async def queue_service(real_redis_client):
    """Create QueueService with REAL Redis."""
    return QueueService(redis_client=real_redis_client)


@pytest.fixture
async def job_service(real_redis_client):
    """Create JobService with REAL Redis."""
    return JobService(redis_client=real_redis_client)


@pytest.fixture
async def approval_service(real_redis_client, real_s3_client, job_service, queue_service):
    """Create ApprovalService with real dependencies."""
    return ApprovalService(
        redis_client=real_redis_client,
        s3_client=real_s3_client,
        job_service=job_service,
        queue_service=queue_service
    )


# ============================================================================
# MOCKED AI/ML FIXTURES - Expensive Components (Keep Mocked)
# ============================================================================

@pytest.fixture(autouse=True)
def mock_anthropic_api():
    """Auto-mock Anthropic API to avoid needing API keys in integration tests."""
    import os
    # Set fake API key to avoid initialization errors
    os.environ['ANTHROPIC_API_KEY'] = 'test-key-for-integration-tests'
    yield
    # Clean up
    if 'ANTHROPIC_API_KEY' in os.environ and os.environ['ANTHROPIC_API_KEY'] == 'test-key-for-integration-tests':
        del os.environ['ANTHROPIC_API_KEY']


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

@pytest.fixture
async def pii_worker(storage_service, queue_service, job_service, mock_pii_analyzer):
    """Create PIIWorker instance with REAL services and MOCKED PII analyzer."""
    from src.services.pii_service import PIIDetectionService

    # Create PIIDetectionService with mocked PII analyzer
    pii_service = PIIDetectionService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )
    # Replace the auto-created analyzer with our mocked one
    pii_service.pii_analyzer = mock_pii_analyzer

    # Create worker and inject the pre-configured pii service
    worker = PIIWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )
    # Replace the auto-created pii_service with our mocked one
    worker.pii_service = pii_service

    return worker


@pytest.fixture
async def processing_worker(storage_service, queue_service, job_service, mock_pdf_converter, mock_ai_enhancement):
    """Create ProcessingWorker instance with REAL services and MOCKED AI."""
    from src.services.processing_service import ProcessingService

    # Create ProcessingService with mocked AI/PDF components
    processing_service = ProcessingService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        pdf_converter=mock_pdf_converter,
        ai_enhancement=mock_ai_enhancement
    )

    # Create worker and inject the pre-configured processing service
    worker = ProcessingWorker(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service
    )
    # Replace the auto-created processing_service with our mocked one
    worker.processing_service = processing_service

    return worker


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
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter

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
