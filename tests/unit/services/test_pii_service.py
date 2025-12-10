"""Unit tests for PIIDetectionService.

Tests PII routing logic: documents with PII go to approval queue,
clean documents go directly to processing queue.

These are Tier 1 tests - catching compliance violations (PII routing wrong).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.pii_service import PIIDetectionService
from src.shared.constants.queues import APPROVAL_QUEUE, PROCESSING_QUEUE
from src.shared.constants.statuses import (
    STATUS_AWAITING_APPROVAL,
    STATUS_PROCESSING,
)
from src.shared.models.pii import PIIFinding

from tests.conftest_fixtures.data_factories import create_pii_queue_payload

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class TestPIIDetectionRouting:
    """Test PII detection routing logic - the core compliance-critical behavior."""

    @pytest.fixture
    def mock_storage_service(self):
        """Mock StorageService that returns fake PDF content."""
        mock = MagicMock()
        mock.download_temp_file = AsyncMock(return_value=b"fake_pdf_content")
        return mock

    @pytest.fixture
    def mock_queue_service(self):
        """Mock QueueService for queue operations."""
        mock = MagicMock()
        mock.enqueue = AsyncMock()
        mock.add_to_timeout_tracking = AsyncMock()
        return mock

    @pytest.fixture
    def mock_job_service(self):
        """Mock JobService for status updates."""
        mock = MagicMock()
        mock.update_job_status = AsyncMock()
        mock.store_approval_token_mapping = AsyncMock()
        return mock

    @pytest.fixture
    def pii_service(self, mock_storage_service, mock_queue_service, mock_job_service):
        """Create PIIDetectionService with mocked dependencies."""
        return PIIDetectionService(
            storage_service=mock_storage_service,
            queue_service=mock_queue_service,
            job_service=mock_job_service,
        )

    async def test_pii_found_routes_to_approval_queue(
        self, pii_service, mock_queue_service, mock_job_service
    ):
        """PDF with detected PII goes to approval queue, not processing.

        This test catches: compliance violations where PII documents
        are processed without human approval.
        """
        job = create_pii_queue_payload()
        pii_finding = PIIFinding(
            entity_type="EMAIL_ADDRESS",
            start=0,
            end=20,
            score=0.95,
            text="student@example.com",
        )

        # Mock PII analyzer to find PII
        with patch.object(
            pii_service.pii_analyzer,
            "analyze_text",
            return_value=[pii_finding],
        ):
            # Mock PDF extraction
            with patch(
                "src.services.pii_service.extract_pdf_text",
                return_value="Contact: student@example.com",
            ):
                await pii_service.process_pii_job(job)

        # Verify: job queued to APPROVAL_QUEUE (not PROCESSING_QUEUE)
        enqueue_calls = mock_queue_service.enqueue.call_args_list
        assert len(enqueue_calls) == 1
        queue_name, payload = enqueue_calls[0][0]
        assert queue_name == APPROVAL_QUEUE

        # Verify: job status set to awaiting_approval
        status_calls = mock_job_service.update_job_status.call_args_list
        final_status_call = status_calls[-1]
        assert final_status_call[0][1] == STATUS_AWAITING_APPROVAL

        # Verify: timeout tracking added (for approval expiration)
        mock_queue_service.add_to_timeout_tracking.assert_called_once()

    async def test_clean_pdf_routes_to_processing_queue(
        self, pii_service, mock_queue_service, mock_job_service
    ):
        """PDF without PII skips approval, goes directly to processing.

        This test catches: clean documents incorrectly going to approval
        queue, causing unnecessary delays.
        """
        job = create_pii_queue_payload()

        # Mock PII analyzer to find NO PII
        with patch.object(
            pii_service.pii_analyzer,
            "analyze_text",
            return_value=[],  # No PII findings
        ):
            # Mock PDF extraction
            with patch(
                "src.services.pii_service.extract_pdf_text",
                return_value="Chapter 1: Introduction to Mathematics",
            ):
                await pii_service.process_pii_job(job)

        # Verify: job queued to PROCESSING_QUEUE (not APPROVAL_QUEUE)
        enqueue_calls = mock_queue_service.enqueue.call_args_list
        assert len(enqueue_calls) == 1
        queue_name, payload = enqueue_calls[0][0]
        assert queue_name == PROCESSING_QUEUE

        # Verify: job status set to processing
        status_calls = mock_job_service.update_job_status.call_args_list
        final_status_call = status_calls[-1]
        assert final_status_call[0][1] == STATUS_PROCESSING

        # Verify: NO timeout tracking (not needed for processing)
        mock_queue_service.add_to_timeout_tracking.assert_not_called()
