"""Integration tests for the by-job-id PII decision endpoints.

Covers POST /api/v1/documents/{job_id}/pii/approve and
POST /api/v1/documents/{job_id}/pii/deny — the variants the Canvas LTI
connector consumes. The token-based equivalent at
/api/v1/approval/{token}/decision is covered by test_approval_flow.py.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from src.dependencies import (
    get_redis_client,
    get_s3_url_service,
    get_storage_service,
)
from src.main import app


@pytest.fixture
def job_id():
    return "550e8400-e29b-41d4-a716-446655440000"


@pytest.fixture
def awaiting_approval_job(job_id):
    return {
        "job_id": job_id,
        "s3_key": "temp/test-doc.pdf",
        "status": "awaiting_approval",
    }


@pytest.fixture
def decided_job(job_id):
    """Job that already moved past awaiting_approval — should trigger 409."""
    return {
        "job_id": job_id,
        "s3_key": "temp/test-doc.pdf",
        "status": "processing_queued",
    }


@pytest.fixture
def post_body():
    return {
        "justification": "Author bylines and citations — not student PII.",
        "reviewed_by": "faculty@example.edu",
    }


def _override_deps(mock_redis: AsyncMock) -> None:
    """Wire dependency_overrides for redis + storage + s3 url services."""
    app.dependency_overrides[get_redis_client] = lambda: mock_redis
    app.dependency_overrides[get_storage_service] = lambda: AsyncMock()
    app.dependency_overrides[get_s3_url_service] = lambda: AsyncMock()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_approve_pii_happy_path(
    job_id, awaiting_approval_job, post_body, api_key_headers
):
    """Happy path: existing awaiting_approval job → 200, status flipped to processing_queued."""
    mock_redis = AsyncMock()
    _override_deps(mock_redis)
    mock_job_service = AsyncMock()
    mock_job_service.get_job.return_value = awaiting_approval_job
    mock_approval_service = AsyncMock()

    try:
        with (
            patch("src.api.documents.JobService", return_value=mock_job_service),
            patch("src.api.documents.ApprovalService", return_value=mock_approval_service),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/documents/{job_id}/pii/approve",
                    json=post_body,
                    headers=api_key_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "approved"
        assert body["job_id"] == job_id
        assert "approved" in body["message"].lower()
        mock_approval_service.quick_approve.assert_awaited_once_with(job_id)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deny_pii_happy_path(
    job_id, awaiting_approval_job, post_body, api_key_headers
):
    """Happy path: deny → 200, quick_deny called once."""
    mock_redis = AsyncMock()
    _override_deps(mock_redis)
    mock_job_service = AsyncMock()
    mock_job_service.get_job.return_value = awaiting_approval_job
    mock_approval_service = AsyncMock()

    try:
        with (
            patch("src.api.documents.JobService", return_value=mock_job_service),
            patch("src.api.documents.ApprovalService", return_value=mock_approval_service),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/documents/{job_id}/pii/deny",
                    json=post_body,
                    headers=api_key_headers,
                )

        assert response.status_code == 200
        body = response.json()
        assert body["decision"] == "denied"
        assert body["job_id"] == job_id
        assert "denied" in body["message"].lower()
        mock_approval_service.quick_deny.assert_awaited_once_with(job_id)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_approve_pii_job_not_found(job_id, post_body, api_key_headers):
    """JobService.get_job returns None → 404."""
    mock_redis = AsyncMock()
    _override_deps(mock_redis)
    mock_job_service = AsyncMock()
    mock_job_service.get_job.return_value = None

    try:
        with patch("src.api.documents.JobService", return_value=mock_job_service):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/documents/{job_id}/pii/approve",
                    json=post_body,
                    headers=api_key_headers,
                )

        assert response.status_code == 404
        assert job_id in response.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_approve_pii_already_decided_returns_409(
    job_id, decided_job, post_body, api_key_headers
):
    """Job exists but isn't in awaiting_approval → 409.

    This is the contract the Canvas LTI connector relies on to surface
    "another instructor already decided in a parallel tab" as a 409 to the
    operator. Without this pre-check, ``quick_approve`` would silently
    overwrite the already-decided state.
    """
    mock_redis = AsyncMock()
    _override_deps(mock_redis)
    mock_job_service = AsyncMock()
    mock_job_service.get_job.return_value = decided_job
    mock_approval_service = AsyncMock()

    try:
        with (
            patch("src.api.documents.JobService", return_value=mock_job_service),
            patch("src.api.documents.ApprovalService", return_value=mock_approval_service),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/documents/{job_id}/pii/approve",
                    json=post_body,
                    headers=api_key_headers,
                )

        assert response.status_code == 409
        assert "processing_queued" in response.json()["detail"]
        mock_approval_service.quick_approve.assert_not_awaited()
    finally:
        app.dependency_overrides.clear()


