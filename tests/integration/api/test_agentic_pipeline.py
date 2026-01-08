"""Integration tests for agentic pipeline API endpoints.

These tests verify the agentic document processing pipeline flow:
1. Document submission with skip_pii_scan and review_mode options
2. Streaming events via SSE
3. Ledger retrieval for PR-like review

Tests use real Redis (via testcontainers) but mock heavy dependencies
like S3, Docling, and Bedrock to avoid external service calls.
"""

import asyncio
import io
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from src.dependencies import (
    get_job_service,
    get_redis_client,
    get_s3_url_service,
    get_storage_service,
)
from src.main import app

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_pdf_bytes():
    """Create minimal valid PDF bytes for testing."""
    return (
        b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 "
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 44 >>\nstream\nBT\n/F1 12 Tf\n100 700 Td\n"
        b"(Test PDF) Tj\nET\nendstream\nendobj\nxref\n0 5\n"
        b"0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n"
        b"0000000115 00000 n\n0000000317 00000 n\ntrailer\n"
        b"<< /Size 5 /Root 1 0 R >>\nstartxref\n410\n%%EOF"
    )


@pytest.fixture
def mock_storage_service():
    """Create mock storage service for S3 operations."""
    mock = MagicMock()
    job_id = str(uuid.uuid4())
    s3_key = f"temp/{job_id}.pdf"

    mock.store_document = AsyncMock(return_value=(job_id, s3_key))
    mock.download_file = AsyncMock(return_value=b"fake pdf content")
    mock.upload_file = AsyncMock()

    return mock, job_id, s3_key


@pytest.fixture
def mock_s3_url_service():
    """Create mock S3 URL service."""
    mock = MagicMock()
    mock.generate_url = AsyncMock(
        side_effect=lambda key, bucket=None: f"http://localhost:4566/{bucket or 'test-bucket'}/{key}"
    )
    mock.generate_presigned_url = AsyncMock(side_effect=lambda key: f"http://localhost:4566/presigned/{key}")
    mock.results_bucket = "equalify-pdf-results"
    mock.temp_bucket = "equalify-pdf-temp"
    return mock


@pytest.fixture
def mock_processing_result():
    """Create mock ProcessingResult from agentic pipeline."""
    from src.agents.models import (
        Ledger,
        LedgerEntry,
        ProcessingResult,
        TaskType,
        VerificationReport,
    )

    # Create sample ledger entries
    ledger = Ledger(document_id="test-doc")
    ledger.append(
        LedgerEntry(
            entry_id="entry-1",
            job_id="job-1",
            page=1,
            action=TaskType.ALT_TEXT,
            target="fig:1",
            before="[Figure 1]",
            after="![Architecture diagram showing system components](fig1.png)",
            reasoning="Added alt-text for accessibility",
            confidence=0.95,
            validated=True,
        )
    )

    return ProcessingResult(
        document_id="test-doc",
        success=True,
        final_markdown="# Test Document\n\nTest content with accessibility improvements.",
        ledger=ledger,
        verification=VerificationReport(
            document_id="test-doc",
            passed=True,
            pages_passed=1,
            pages_failed=0,
        ),
        total_pages=1,
        total_edits=1,
        total_jobs=1,
        total_input_tokens=1000,
        total_output_tokens=500,
        total_cost=0.05,
    )


@pytest.fixture
def mock_event_bus():
    """Create mock EventBus for streaming tests."""
    from src.agents.events import (
        DoclingCompleteEvent,
        DoclingStartedEvent,
        EventBus,
        PlanningCompleteEvent,
        PlanningStartedEvent,
        ProcessingCompleteEvent,
    )

    bus = EventBus("test-doc")

    # Pre-populate with some events
    bus.emit(DoclingStartedEvent(document_id="test-doc", filename="test.pdf"))
    bus.emit(
        DoclingCompleteEvent(
            document_id="test-doc",
            pages_extracted=1,
            images_extracted=1,
            duration_ms=1000,
            is_scanned=False,
            chars_per_page=500.0,
        )
    )
    bus.emit(PlanningStartedEvent(document_id="test-doc", total_pages=1, filename="test.pdf"))
    bus.emit(
        PlanningCompleteEvent(
            document_id="test-doc",
            total_jobs=1,
            structure_jobs=0,
            content_jobs=1,
            dictionary_terms=["test"],
            duration_ms=500,
        )
    )
    bus.emit(
        ProcessingCompleteEvent(
            document_id="test-doc",
            success=True,
            total_edits=1,
            total_jobs=1,
            total_cost=0.05,
            total_duration_ms=2000,
            result_url="http://localhost:4566/results/test-doc.md",
        )
    )

    return bus


# =============================================================================
# Test: Submit with skip_pii_scan=True, review_mode='auto'
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_document_skip_pii_scan_auto_mode(
    sample_pdf_bytes,
    mock_storage_service,
    mock_s3_url_service,
    mock_processing_result,
    real_redis_client,
    api_key_headers,
):
    """Test document submission with skip_pii_scan=True and review_mode='auto'.

    Verifies:
    1. Job is created with status='processing' (not pii_scanning)
    2. Response includes stream_url for SSE
    3. Job record has correct flags (pii_skipped, review_mode)
    """
    mock_storage, job_id, s3_key = mock_storage_service

    # Create mock job service with real Redis behavior
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)

    # Mock DocumentProcessingService.process_document to avoid Docling/Bedrock
    with patch(
        "src.api.documents.DocumentProcessingService.process_document",
        new_callable=AsyncMock,
        return_value=mock_processing_result,
    ):
        # Override dependencies
        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: job_service
        app.dependency_overrides[get_redis_client] = lambda: real_redis_client
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                files = {"file": ("test.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
                data = {
                    "skip_pii_scan": "true",
                    "skip_reason": "Integration test - pre-scanned",
                    "review_mode": "auto",
                }

                response = await client.post(
                    "/api/v1/documents/submit",
                    files=files,
                    data=data,
                    headers=api_key_headers,
                )

            # Verify response
            assert response.status_code == 201, f"Unexpected status: {response.text}"
            response_data = response.json()

            # Status should be 'processing' (not 'pii_scanning')
            assert response_data["status"] == "processing"
            assert response_data["job_id"] == job_id

            # Stream URL should be included for agentic pipeline
            assert "stream_url" in response_data
            assert f"/api/v1/documents/{job_id}/stream" in response_data["stream_url"]

            # Allow time for background task to create job
            await asyncio.sleep(0.1)

            # Verify job record was created correctly
            job = await job_service.get_job(job_id)
            assert job is not None
            assert job["status"] == "processing"
            assert job.get("pii_skipped") == "true"
            assert job.get("review_mode") == "auto"

        finally:
            app.dependency_overrides.clear()


# =============================================================================
# Test: Submit with skip_pii_scan=True, review_mode='human'
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_submit_document_skip_pii_scan_human_mode(
    sample_pdf_bytes,
    mock_storage_service,
    mock_s3_url_service,
    mock_processing_result,
    real_redis_client,
    api_key_headers,
):
    """Test document submission with skip_pii_scan=True and review_mode='human'.

    Verifies:
    1. Job is created with review_mode='human'
    2. When completed, response includes ledger_url for PR-like review
    """
    mock_storage, job_id, s3_key = mock_storage_service

    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)

    # Mock the processing service to complete the job
    async def mock_process(*args, **kwargs):
        # Update job to completed state as the service would
        await job_service.update_job_status(
            job_id,
            status="completed",
            processing_phase="complete",
            result_url=f"results/{job_id}/result.md",
            ledger_s3_key=f"results/{job_id}/ledger.json",
            confidence_score="0.85",
            total_edits="1",
            total_pages="1",
        )
        return mock_processing_result

    with patch(
        "src.api.documents.DocumentProcessingService.process_document",
        new_callable=AsyncMock,
        side_effect=mock_process,
    ):
        app.dependency_overrides[get_storage_service] = lambda: mock_storage
        app.dependency_overrides[get_job_service] = lambda: job_service
        app.dependency_overrides[get_redis_client] = lambda: real_redis_client
        app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                # Submit document
                files = {"file": ("test.pdf", io.BytesIO(sample_pdf_bytes), "application/pdf")}
                data = {
                    "skip_pii_scan": "true",
                    "review_mode": "human",
                }

                response = await client.post(
                    "/api/v1/documents/submit",
                    files=files,
                    data=data,
                    headers=api_key_headers,
                )

            assert response.status_code == 201
            response_data = response.json()
            assert response_data["status"] == "processing"

            # Allow background task to process
            await asyncio.sleep(0.2)

            # Check job status after processing
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                status_response = await client.get(
                    f"/api/v1/documents/{job_id}",
                    headers=api_key_headers,
                )

            assert status_response.status_code == 200
            status_data = status_response.json()
            assert status_data["status"] == "completed"
            assert status_data["review_mode"] == "human"

            # ledger_url should be present for human review mode
            assert "ledger_url" in status_data
            assert f"/api/v1/documents/{job_id}/ledger" in status_data["ledger_url"]

        finally:
            app.dependency_overrides.clear()


# =============================================================================
# Test: Stream endpoint integration
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stream_endpoint_for_completed_job(
    mock_event_bus,
    real_redis_client,
    api_key_headers,
):
    """Test streaming events via SSE for a completed job.

    Verifies:
    1. Stream endpoint returns SSE format
    2. Events are delivered for completed jobs
    3. Final 'done' event is sent
    """
    from src.agents.events import register_event_bus
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)
    job_id = "test-stream-job-" + str(uuid.uuid4())[:8]

    # Create completed job
    await job_service.create_job(
        job_id=job_id,
        s3_key=f"temp/{job_id}.pdf",
        status="completed",
        original_filename="test.pdf",
        review_mode="auto",
    )

    # Register event bus in the global registry
    register_event_bus(job_id, mock_event_bus)

    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/documents/{job_id}/stream",
                headers=api_key_headers,
            )

        # SSE endpoint returns text/event-stream
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        # Parse SSE content
        content = response.text
        assert "event:" in content
        assert "data:" in content

        # Should have emitted events including final done
        assert "done" in content or "processing:complete" in content

    finally:
        app.dependency_overrides.clear()
        # Clean up event bus registry
        from src.agents.events import unregister_event_bus

        unregister_event_bus(job_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_stream_endpoint_returns_404_for_nonexistent_job(
    real_redis_client,
    api_key_headers,
):
    """Test that stream endpoint returns 404 for non-existent job."""
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)

    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/documents/nonexistent-job-id/stream",
                headers=api_key_headers,
            )

        assert response.status_code == 404

    finally:
        app.dependency_overrides.clear()


# =============================================================================
# Test: Ledger endpoint integration
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ledger_endpoint_returns_changes(
    mock_storage_service,
    mock_s3_url_service,
    real_redis_client,
    api_key_headers,
):
    """Test ledger endpoint returns change ledger for PR-like review.

    Verifies:
    1. Ledger endpoint returns 200 for completed job
    2. Response includes ledger entries grouped by page
    3. Each entry has required fields (action, before, after, reasoning)
    """
    mock_storage, _, _ = mock_storage_service

    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)
    job_id = "test-ledger-job-" + str(uuid.uuid4())[:8]

    # Create completed job first
    await job_service.create_job(
        job_id=job_id,
        s3_key=f"temp/{job_id}.pdf",
        status="completed",
        original_filename="test.pdf",
        review_mode="human",
    )
    # Then update with additional fields
    await job_service.update_job_status(
        job_id,
        status="completed",
        result_url=f"results/{job_id}/result.md",
        ledger_s3_key=f"results/{job_id}/ledger.json",
        total_pages="3",
        total_edits="2",
    )

    # Mock ledger data from S3
    ledger_data = {
        "document_id": job_id,
        "entries": [
            {
                "entry_id": "entry-1",
                "job_id": "job-1",
                "page": 1,
                "timestamp": datetime.now(UTC).isoformat(),
                "action": "alt_text",
                "target": "fig:1",
                "before": "[Figure 1]",
                "after": "![Architecture diagram](fig1.png)",
                "reasoning": "Added descriptive alt-text",
                "confidence": 0.95,
                "validated": True,
            },
            {
                "entry_id": "entry-2",
                "job_id": "job-2",
                "page": 2,
                "timestamp": datetime.now(UTC).isoformat(),
                "action": "heading_fix",
                "target": "heading-2.1",
                "before": "### Section 2.1",
                "after": "## Section 2.1",
                "reasoning": "Fixed heading level to maintain hierarchy",
                "confidence": 0.9,
                "validated": True,
            },
        ],
        "total_edits": 2,
    }

    mock_storage.download_file = AsyncMock(return_value=json.dumps(ledger_data).encode("utf-8"))

    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client
    app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

        assert response.status_code == 200
        data = response.json()

        # Verify response structure
        assert data["job_id"] == job_id
        assert data["total_edits"] == 2
        assert data["pages_with_changes"] == 2

        # Verify pages are grouped
        assert "pages" in data
        assert len(data["pages"]) == 2

        # Verify page 1 entries
        page1 = next(p for p in data["pages"] if p["page"] == 1)
        assert page1["edit_count"] == 1
        assert len(page1["entries"]) == 1
        assert page1["entries"][0]["action"] == "alt_text"
        assert page1["entries"][0]["target"] == "fig:1"

        # Verify page 2 entries
        page2 = next(p for p in data["pages"] if p["page"] == 2)
        assert page2["edit_count"] == 1
        assert page2["entries"][0]["action"] == "heading_fix"

        # Verify final markdown URL is present
        assert "final_markdown_url" in data

    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ledger_endpoint_returns_400_for_incomplete_job(
    real_redis_client,
    api_key_headers,
):
    """Test ledger endpoint returns 400 if job is not yet completed."""
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)
    job_id = "test-incomplete-job-" + str(uuid.uuid4())[:8]

    # Create processing job (not completed)
    await job_service.create_job(
        job_id=job_id,
        s3_key=f"temp/{job_id}.pdf",
        status="processing",
        original_filename="test.pdf",
        review_mode="human",
    )

    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

        assert response.status_code == 400
        assert "not yet complete" in response.json()["detail"].lower()

    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ledger_endpoint_returns_404_for_nonexistent_job(
    real_redis_client,
    api_key_headers,
):
    """Test ledger endpoint returns 404 for non-existent job."""
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)

    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                "/api/v1/documents/nonexistent-job/ledger",
                headers=api_key_headers,
            )

        assert response.status_code == 404

    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ledger_endpoint_returns_404_when_ledger_not_found(
    mock_storage_service,
    mock_s3_url_service,
    real_redis_client,
    api_key_headers,
):
    """Test ledger endpoint returns 404 when ledger file doesn't exist in S3."""
    mock_storage, _, _ = mock_storage_service

    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)
    job_id = "test-no-ledger-job-" + str(uuid.uuid4())[:8]

    # Create completed job but no ledger in S3
    await job_service.create_job(
        job_id=job_id,
        s3_key=f"temp/{job_id}.pdf",
        status="completed",
        original_filename="test.pdf",
        review_mode="human",
    )

    # Mock S3 download to fail (ledger doesn't exist)
    mock_storage.download_file = AsyncMock(side_effect=Exception("Key not found"))

    app.dependency_overrides[get_storage_service] = lambda: mock_storage
    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client
    app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/documents/{job_id}/ledger",
                headers=api_key_headers,
            )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    finally:
        app.dependency_overrides.clear()


# =============================================================================
# Test: Status endpoint with agentic pipeline responses
# =============================================================================


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_endpoint_returns_agentic_processing_response(
    real_redis_client,
    mock_s3_url_service,
    api_key_headers,
):
    """Test status endpoint returns AgenticProcessingResponse during processing.

    Verifies:
    1. Response includes review_mode and processing_phase
    2. Response includes stream_url
    3. Response includes job progress (jobs_total, jobs_complete)
    """
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)
    job_id = "test-agentic-processing-" + str(uuid.uuid4())[:8]

    # Create processing job first
    await job_service.create_job(
        job_id=job_id,
        s3_key=f"temp/{job_id}.pdf",
        status="processing",
        original_filename="test.pdf",
        review_mode="auto",
        pii_skipped=True,
    )
    # Then update with agentic pipeline fields
    await job_service.update_job_status(
        job_id,
        status="processing",
        processing_phase="executing",
        jobs_total="5",
        jobs_complete="2",
    )

    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client
    app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/documents/{job_id}",
                headers=api_key_headers,
            )

        assert response.status_code == 200
        data = response.json()

        # Should be AgenticProcessingResponse
        assert data["status"] == "processing"
        assert data["review_mode"] == "auto"
        assert data["processing_phase"] == "executing"
        assert data["jobs_total"] == 5
        assert data["jobs_complete"] == 2
        assert "stream_url" in data
        assert f"/api/v1/documents/{job_id}/stream" in data["stream_url"]
        assert data.get("pii_skipped") is True

    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_endpoint_returns_agentic_completed_response(
    real_redis_client,
    mock_s3_url_service,
    api_key_headers,
):
    """Test status endpoint returns AgenticCompletedResponse when completed.

    Verifies:
    1. Response includes markdown_url, confidence_score, llm_cost
    2. For human mode: includes ledger_url
    3. For auto mode: ledger_url is null
    """
    from src.services.job_service import JobService

    job_service = JobService(redis_client=real_redis_client)

    # Test human mode (should have ledger_url)
    job_id_human = "test-completed-human-" + str(uuid.uuid4())[:8]
    await job_service.create_job(
        job_id=job_id_human,
        s3_key=f"temp/{job_id_human}.pdf",
        status="completed",
        original_filename="test.pdf",
        review_mode="human",
    )
    await job_service.update_job_status(
        job_id_human,
        status="completed",
        result_url=f"results/{job_id_human}/result.md",
        confidence_score="0.85",
        total_pages="5",
        total_edits="12",
        llm_cost_cents="5.5",
        llm_input_tokens="10000",
        llm_output_tokens="5000",
        llm_total_tokens="15000",
    )

    # Test auto mode (should not have ledger_url)
    job_id_auto = "test-completed-auto-" + str(uuid.uuid4())[:8]
    await job_service.create_job(
        job_id=job_id_auto,
        s3_key=f"temp/{job_id_auto}.pdf",
        status="completed",
        original_filename="test.pdf",
        review_mode="auto",
    )
    await job_service.update_job_status(
        job_id_auto,
        status="completed",
        result_url=f"results/{job_id_auto}/result.md",
        confidence_score="0.9",
        total_pages="3",
        total_edits="8",
    )

    app.dependency_overrides[get_job_service] = lambda: job_service
    app.dependency_overrides[get_redis_client] = lambda: real_redis_client
    app.dependency_overrides[get_s3_url_service] = lambda: mock_s3_url_service

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Test human mode
            response_human = await client.get(
                f"/api/v1/documents/{job_id_human}",
                headers=api_key_headers,
            )

            assert response_human.status_code == 200
            data_human = response_human.json()
            assert data_human["status"] == "completed"
            assert data_human["review_mode"] == "human"
            assert "markdown_url" in data_human
            assert data_human["confidence_score"] == 0.85
            assert data_human["total_pages"] == 5
            assert data_human["total_edits"] == 12

            # Human mode should have ledger_url
            assert "ledger_url" in data_human
            assert data_human["ledger_url"] is not None
            assert f"/api/v1/documents/{job_id_human}/ledger" in data_human["ledger_url"]

            # LLM cost should be present
            assert "llm_cost" in data_human
            assert data_human["llm_cost"]["estimated_cost_cents"] == 5.5

            # Test auto mode
            response_auto = await client.get(
                f"/api/v1/documents/{job_id_auto}",
                headers=api_key_headers,
            )

            assert response_auto.status_code == 200
            data_auto = response_auto.json()
            assert data_auto["status"] == "completed"
            assert data_auto["review_mode"] == "auto"

            # Auto mode should NOT have ledger_url
            assert data_auto.get("ledger_url") is None

    finally:
        app.dependency_overrides.clear()
