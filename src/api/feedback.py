"""Feedback API endpoints.

Thin public proxy that the pipeline viewer SPA posts to when a user submits
feedback on a processed document. Forwards to the external feedback service
using a server-side API key — the browser never sees the key.

Issue reports only (v1). Text corrections come later.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..config import settings
from ..services.feedback_client import feedback_client

router = APIRouter(prefix="/api/v1/feedback", tags=["Feedback"])


FeedbackCategory = Literal["content", "formatting", "accessibility", "structure", "other"]


class FeedbackConfig(BaseModel):
    enabled: bool = Field(..., description="Whether the viewer should surface a feedback UI")


class IssueReportInput(BaseModel):
    category: FeedbackCategory = Field(default="other")
    description: str = Field(..., min_length=10, max_length=5000)
    session_id: str | None = Field(default=None, description="Pipeline session ID")
    document_title: str | None = Field(default=None, max_length=500)
    page: int | None = Field(default=None, ge=1)
    section: str | None = Field(default=None, max_length=255)
    metadata: dict[str, Any] | None = None
    # Honeypot: real users leave this blank. Bots fill every field.
    website: str | None = Field(default=None, max_length=255)


@router.get("/config", response_model=FeedbackConfig)
async def get_feedback_config() -> FeedbackConfig:
    """Tell the viewer whether the feedback service is wired up."""
    return FeedbackConfig(
        enabled=bool(settings.feedback_enabled and settings.feedback_service_url)
    )


@router.post("", status_code=202)
async def submit_feedback(payload: IssueReportInput) -> dict[str, str]:
    """Forward a user-submitted issue report to the feedback service.

    Fire-and-forget: the feedback client swallows upstream errors so a flaky
    feedback service never breaks the viewer.
    """
    if payload.website:
        # Honeypot tripped — respond as if accepted so bots don't learn.
        return {"status": "accepted"}

    if not (settings.feedback_enabled and settings.feedback_service_url):
        raise HTTPException(status_code=503, detail="Feedback service is not configured")

    await feedback_client.report_issue(
        document_id=payload.session_id,
        document_title=payload.document_title,
        category=payload.category,
        description=payload.description,
        page=payload.page,
        section=payload.section,
        metadata=payload.metadata,
    )
    return {"status": "accepted"}
