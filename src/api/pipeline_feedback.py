"""API endpoints for pipeline feedback sessions.

Provides a GitHub PR review-style feedback system: humans submit batched
reviews (direct edits + LLM comments), review candidate changes per-item
(accept/reject), and iterate until satisfied.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services.pipeline_viewer import PipelineViewerService
from ..services.pipeline_viewer_models import (
    CandidateChange,
    ChangeDecision,
    ChangeReview,
    FeedbackItem,
    ReviewAction,
)
from ..services.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/pipeline/sessions",
    tags=["Pipeline Feedback"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class FeedbackRequest(BaseModel):
    """Request body for submitting a feedback batch."""

    items: list[FeedbackItem] = Field(..., min_length=1)


class FeedbackResponse(BaseModel):
    """Response from feedback submission."""

    candidates: list[CandidateChange]
    revision_round: int


class ReviewRequest(BaseModel):
    """Request body for reviewing candidate changes."""

    reviews: list[ChangeReview] = Field(..., min_length=1)
    action: ReviewAction = ReviewAction.REQUEST_CHANGES


class ReviewResponse(BaseModel):
    """Response from change review."""

    applied_count: int
    rejected_count: int
    new_version: str | None = None
    rejection_comments: list[str]
    finalized: bool


class ApproveResponse(BaseModel):
    """Response from finalizing a session."""

    finalized: bool
    final_version: str


class SessionStateResponse(BaseModel):
    """Current state of a feedback session."""

    session_id: str
    revision_round: int
    finalized: bool
    versions: list[str]
    latest_markdown: str
    pending_candidates: int
    feedback_rounds: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _get_session(session_id: str):
    """Retrieve a session or raise 404."""
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found or expired")
    return session


@router.post("/{session_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(session_id: str, request: FeedbackRequest):
    """Submit a batch of feedback items (edits + comments).

    Returns candidate changes for human review.
    """
    session = _get_session(session_id)

    if session.finalized:
        raise HTTPException(
            status_code=409, detail="Session is finalized — no more feedback allowed"
        )

    service = PipelineViewerService()

    candidates = await service.process_feedback_batch(
        items=request.items,
        result=session.result,
        structure=session.structure,
        section_map=session.section_map,
        feedback_history=session.feedback_history,
    )

    # Store candidates and feedback round
    session.candidate_changes = candidates
    session.feedback_history.append(request.items)
    session.revision_round += 1

    return FeedbackResponse(
        candidates=candidates,
        revision_round=session.revision_round,
    )


@router.post("/{session_id}/review", response_model=ReviewResponse)
async def review_changes(session_id: str, request: ReviewRequest):
    """Submit per-change decisions (accept/reject).

    Applies accepted changes to produce a new version. Rejection comments
    are collected for the next feedback round.
    """
    session = _get_session(session_id)

    if session.finalized:
        raise HTTPException(
            status_code=409, detail="Session is finalized — no more reviews allowed"
        )

    if not session.candidate_changes:
        raise HTTPException(
            status_code=409, detail="No candidate changes to review"
        )

    # Build lookup of candidates
    candidate_map = {c.id: c for c in session.candidate_changes}

    accepted: list[CandidateChange] = []
    rejection_comments: list[str] = []

    for review in request.reviews:
        candidate = candidate_map.get(review.change_id)
        if candidate is None:
            logger.warning(f"Review for unknown change_id: {review.change_id}")
            continue

        if review.decision == ChangeDecision.ACCEPT:
            accepted.append(candidate)
        elif review.decision == ChangeDecision.REJECT and review.comment:
            rejection_comments.append(review.comment)

    # Apply accepted changes
    new_version = None
    if accepted:
        service = PipelineViewerService()
        step_result = service.apply_candidate_changes(
            accepted_changes=accepted,
            result=session.result,
            revision_round=session.revision_round,
        )
        new_version = step_result.version_after

    # Clear candidates after review
    session.candidate_changes = []

    # Auto-finalize if action is APPROVE
    if request.action == ReviewAction.APPROVE:
        session.finalized = True

    return ReviewResponse(
        applied_count=len(accepted),
        rejected_count=len(request.reviews) - len(accepted),
        new_version=new_version,
        rejection_comments=rejection_comments,
        finalized=session.finalized,
    )


@router.post("/{session_id}/approve", response_model=ApproveResponse)
async def approve_session(session_id: str):
    """Finalize the current version. No more feedback rounds."""
    session = _get_session(session_id)

    if session.finalized:
        raise HTTPException(
            status_code=409, detail="Session is already finalized"
        )

    session.finalized = True

    # Get the latest version key
    version_nums = [
        int(k[1:]) for k in session.result.versions
        if k.startswith("v") and k[1:].isdigit()
    ]
    latest_version = f"v{max(version_nums)}" if version_nums else "v0"

    return ApproveResponse(
        finalized=True,
        final_version=latest_version,
    )


@router.get("/{session_id}/state", response_model=SessionStateResponse)
async def get_session_state(session_id: str):
    """Get the current state of a feedback session."""
    session = _get_session(session_id)

    # Get latest markdown
    latest_md = PipelineViewerService._get_latest_version(session.result)

    # Get sorted version keys
    versions = sorted(
        (k for k in session.result.versions if k.startswith("v") and k[1:].isdigit()),
        key=lambda k: int(k[1:]),
    )

    return SessionStateResponse(
        session_id=session.session_id,
        revision_round=session.revision_round,
        finalized=session.finalized,
        versions=versions,
        latest_markdown=latest_md,
        pending_candidates=len(session.candidate_changes),
        feedback_rounds=len(session.feedback_history),
    )
