from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import settings

logger = logging.getLogger(__name__)


class FeedbackClient:
    """Fire-and-forget client for the Equalify feedback collection service.
    
    Never blocks or fails the main app. Errors are logged and swallowed.
    """

    def __init__(self) -> None:
        self._enabled = settings.feedback_enabled and bool(settings.feedback_service_url)
        self._base_url = settings.feedback_service_url or ""
        self._api_key = settings.feedback_service_api_key.get_secret_value() if settings.feedback_service_api_key else ""

    async def _post(self, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
        if not self._enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                if isinstance(payload, list):
                    body: dict[str, Any] = {"items": payload}
                else:
                    body = payload
                await client.post(
                    f"{self._base_url}/api/v1/feedback",
                    json=body,
                    headers={settings.api_key_header_name: self._api_key},
                )
        except Exception:
            logger.warning("Failed to send feedback to collection service", exc_info=True)

    async def upload_document(self, content: bytes, filename: str) -> str | None:
        """Upload a PDF to the feedback service for archival.

        Returns the document ref on success, None on failure (fire-and-forget).
        """
        if not self._enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/documents",
                    files={"file": (filename, content, "application/pdf")},
                    headers={settings.api_key_header_name: self._api_key},
                )
                if resp.status_code == 200:
                    return resp.json().get("ref")
                logger.warning("Document upload returned status %d", resp.status_code)
        except Exception:
            logger.warning("Failed to upload document to feedback service", exc_info=True)
        return None

    async def track_edit(
        self,
        *,
        document_id: str | None = None,
        document_title: str | None = None,
        document_ref: str | None = None,
        original_text: str,
        corrected_text: str,
        page: int | None = None,
        section: str | None = None,
        category: str = "content",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._post({
            "document_id": document_id,
            "document_title": document_title,
            "document_ref": document_ref,
            "feedback_type": "user_edit",
            "category": category,
            "original_text": original_text,
            "corrected_text": corrected_text,
            "page": page,
            "section": section,
            "metadata": metadata,
        })

    async def report_issue(
        self,
        *,
        document_id: str | None = None,
        document_title: str | None = None,
        document_ref: str | None = None,
        category: str = "other",
        description: str,
        page: int | None = None,
        section: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._post({
            "document_id": document_id,
            "document_title": document_title,
            "document_ref": document_ref,
            "feedback_type": "issue_report",
            "category": category,
            "description": description,
            "page": page,
            "section": section,
            "metadata": metadata,
        })

    async def track_edits_batch(self, edits: list[dict[str, Any]]) -> None:
        if not edits:
            return
        await self._post(edits)


# Singleton
feedback_client = FeedbackClient()
