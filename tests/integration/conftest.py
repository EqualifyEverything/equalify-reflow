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
from src.services.storage_service import StorageService
from src.shared.models.pii import PIIFinding
from src.workers.pii_worker import PIIWorker
from src.workers.processing_worker import ProcessingWorker
from testcontainers.localstack import LocalStackContainer
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
def localstack_container():
    """Session-scoped LocalStack container via testcontainers.

    Container starts once per test session, providing isolated S3 service.
    """
    with LocalStackContainer(image="localstack/localstack:latest") as localstack:
        localstack.with_services("s3")
        yield localstack


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
def real_s3_client(localstack_container):
    """Real S3 client connected to LocalStack testcontainer with per-test cleanup.

    Each test gets a fresh S3 environment with buckets pre-created.
    Testcontainers handles container lifecycle and cleanup.
    """
    # Build LocalStack endpoint URL from testcontainer
    host = localstack_container.get_container_host_ip()
    port = localstack_container.get_exposed_port(4566)
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
    """Create StorageService with REAL S3 (testcontainer LocalStack)."""
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
def mock_ai_agents(request):
    """Auto-mock all AI agents for integration tests (no API keys needed).

    This fixture mocks the multi-agent pipeline:
    - AnalysisAgent: Returns mock DocumentManifest and observations
    - ExtractionAgent: Returns mock markdown content
    - Specialized agents (FiguresAgent, TablesAgent, etc.): Skipped via empty required_agents

    NOTE: This fixture is EXCLUDED for test_bedrock_agent.py tests which
    are designed to test real Bedrock API integration.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from src.shared.models.processing import LLMUsage
    from src.shared.models.remediation import DocumentManifest, PageFeatures

    # Skip mocking for bedrock integration tests (they test real Bedrock)
    if "test_bedrock_agent" in request.node.nodeid:
        yield
        return

    # Create mock LLM usage for cost tracking
    mock_usage = LLMUsage(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost_cents=0.01
    )

    # Create mock DocumentManifest (output of AnalysisAgent)
    import json
    heading_tree_data = {
        "document_title": "Test Document",
        "title_page": 1,
        "sections": [],
        "total_pages": 1,
        "layout_type": "single_column",
        "confidence": 0.95,
        "observations": ""
    }
    mock_manifest = DocumentManifest(
        job_id="test-job-id",
        document_title="Test Document",
        document_type="lecture_notes",
        total_pages=1,
        analysis_model="mock-model",
        heading_tree_json=json.dumps(heading_tree_data),
        page_features=[
            PageFeatures(
                page_num=1,
                has_images=False,
                image_count=0,
                has_tables=False,
                table_count=0,
                has_lists=False,
                has_code_blocks=False,
                has_math=False,
                layout_type="single_column",
                has_headers_footers=False,
                complexity_score=0.3,
                complexity_factors=[]
            )
        ],
        required_agents=[],  # Empty = skip specialized agents
        analysis_confidence=0.95
    )

    # Mock AnalysisAgent
    with patch('src.agents.analysis_agent.AnalysisAgent') as mock_analysis_class:
        mock_analysis = MagicMock()
        # analyze() returns (manifest, observations, usage)
        mock_analysis.analyze = AsyncMock(return_value=(mock_manifest, [], mock_usage))
        mock_analysis_class.return_value = mock_analysis

        # Mock ExtractionAgent
        with patch('src.agents.extraction_agent.ExtractionAgent') as mock_extraction_class:
            mock_extraction = MagicMock()
            # extract() returns (markdown, confidence, usage)
            mock_extraction.extract = AsyncMock(return_value=(
                "# Test Document\n\nThis is mock extracted content.",
                0.92,
                mock_usage
            ))
            mock_extraction_class.return_value = mock_extraction

            # Mock ConsolidationService (converts observations to proposals)
            with patch('src.services.consolidation_service.ConsolidationService') as mock_consolidation_class:
                mock_consolidation = MagicMock()
                # consolidate() returns (proposals, usage)
                mock_consolidation.consolidate = AsyncMock(return_value=([], mock_usage))
                mock_consolidation_class.return_value = mock_consolidation

                # Also patch at the ProcessingService module level for imports
                with patch('src.services.processing_service.AnalysisAgent', mock_analysis_class):
                    with patch('src.services.processing_service.ExtractionAgent', mock_extraction_class):
                        with patch('src.services.processing_service.ConsolidationService', mock_consolidation_class):
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
async def processing_worker(storage_service, queue_service, job_service, mock_pdf_converter, real_redis_client):
    """Create ProcessingWorker instance with REAL services and MOCKED PDF converter.

    The ProcessingService uses the new analysis+extraction pipeline which
    calls various AI agents. These are mocked via mock_ai_settings.
    """
    from src.services.processing_service import ProcessingService

    # Create ProcessingService with mocked PDF converter
    # AI agents are mocked via the mock_ai_settings autouse fixture
    processing_service = ProcessingService(
        storage_service=storage_service,
        queue_service=queue_service,
        job_service=job_service,
        redis_client=real_redis_client,
        pdf_converter=mock_pdf_converter,
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
