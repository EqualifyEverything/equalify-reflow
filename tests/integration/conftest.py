"""Shared fixtures for integration tests.

Provides fixtures for:
- Real Redis and S3 clients connected to Docker services
- Service instances with real infrastructure (mocked AI only)
- Test data generators
- Cleanup helpers
"""

import pytest
import pytest_asyncio
import uuid
import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import AsyncGenerator
import redis.asyncio as aioredis
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
# DOCKER SERVICE FIXTURES - Real Infrastructure
# ============================================================================

@pytest_asyncio.fixture
async def real_redis_client(request) -> AsyncGenerator[aioredis.Redis, None]:
    """Real Redis client connected to Docker service with per-test cleanup.

    When running tests in parallel with pytest-xdist, each worker gets a separate
    Redis database (0-15) to avoid race conditions from flushall() cleanup.
    """
    # Get worker ID from pytest-xdist (e.g., "gw0", "gw1", "gw2", "gw3")
    # If not using xdist, worker_id will be "master"
    worker_id = getattr(request.config, 'workerinput', {}).get('workerid', 'master')

    # Map worker ID to Redis database number (0-15)
    if worker_id == 'master':
        db = 0  # Single-threaded test run
    else:
        # Extract number from "gw0", "gw1", etc.
        db = int(worker_id.replace('gw', '')) % 16  # Redis has DBs 0-15

    # Connect to specific Redis database for this worker
    redis_url = settings.redis_url
    if '?' in redis_url:
        redis_url = f"{redis_url}&db={db}"
    else:
        redis_url = f"{redis_url}/{db}"

    client = await aioredis.from_url(redis_url, decode_responses=True)

    yield client

    # Cleanup: flush only THIS database (not all databases)
    await client.flushdb()  # Changed from flushall() to flushdb()
    await client.aclose()


@pytest.fixture
def real_s3_client():
    """Real S3 client connected to LocalStack Docker service with per-test cleanup."""
    # Connect to localstack service in docker-compose network
    # AWS_ENDPOINT_URL is set in docker-compose.dev.yml
    endpoint_url = settings.aws_endpoint_url

    # Use SYNC boto3 client (not async) - matches existing StorageService
    # IMPORTANT: Temporarily unset AWS_PROFILE to prevent boto3 from trying to load profiles
    import os
    aws_profile_backup = os.environ.get('AWS_PROFILE')
    if 'AWS_PROFILE' in os.environ:
        del os.environ['AWS_PROFILE']

    try:
        s3_client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id="test",
            aws_secret_access_key="test",
            region_name="us-east-1",
        )
    finally:
        # Restore AWS_PROFILE if it was set
        if aws_profile_backup is not None:
            os.environ['AWS_PROFILE'] = aws_profile_backup

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
    """Create StorageService with REAL S3 (LocalStack)."""
    return StorageService(
        s3_client=real_s3_client,
        temp_bucket=settings.s3_temp_bucket,
        results_bucket=settings.s3_results_bucket,
    )


@pytest_asyncio.fixture
async def queue_service(real_redis_client):
    """Create QueueService with REAL Redis."""
    return QueueService(redis_client=real_redis_client)


@pytest_asyncio.fixture
async def job_service(real_redis_client):
    """Create JobService with REAL Redis."""
    return JobService(redis_client=real_redis_client)


@pytest_asyncio.fixture
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
def mock_ai_settings():
    """Auto-mock AI settings for Bedrock (no API keys needed)."""
    from unittest.mock import patch, MagicMock

    # Mock settings to use bedrock provider
    with patch('src.agents.accessibility_agent.settings') as mock_settings:
        mock_settings.ai_provider = 'bedrock'
        mock_settings.bedrock_model_id = 'anthropic.claude-3-haiku-20240307-v1:0'
        mock_settings.bedrock_region = 'us-east-1'
        mock_settings.claude_max_tokens = 4096
        mock_settings.claude_temperature = 0.2

        # Mock BedrockConverseModel and Agent to avoid AWS/API calls
        with patch('pydantic_ai.models.bedrock.BedrockConverseModel'):
            with patch('src.agents.accessibility_agent.Agent') as MockAgent:
                # Make Agent() return a mock with a run method
                mock_agent = MagicMock()
                MockAgent.return_value = mock_agent
                yield


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

@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
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
