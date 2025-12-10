"""Correction approval workflow API endpoints.

Provides endpoints for reviewing text correction results and
submitting approval/rejection decisions.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..dependencies import (
    get_correction_approval_service,
    get_job_service,
    get_s3_url_service,
)
from ..services.correction_approval_service import CorrectionApprovalService
from ..services.job_service import JobService
from ..services.s3_url_service import S3URLService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/corrections", tags=["Corrections"])


class CorrectionSummary(BaseModel):
    """Summary of a single correction.

    Attributes:
        page: Page number where correction was made
        type: Type of correction (heading_level, list_structure, etc.)
        original_snippet: First 200 chars of original text
        corrected_snippet: First 200 chars of corrected text
        confidence: AI confidence in this correction (0.0-1.0)
        explanation: Human-readable explanation of the correction

    Example:
        >>> summary = CorrectionSummary(
        ...     page=1,
        ...     type="heading_level",
        ...     original_snippet="Course Schedule",
        ...     corrected_snippet="## Course Schedule",
        ...     confidence=0.95,
        ...     explanation="Visual layout shows level 2 heading"
        ... )
    """
    page: int = Field(..., ge=1, description="Page number (1-indexed)")
    type: str = Field(..., description="Correction type")
    original_snippet: str = Field(..., max_length=200, description="Original text (first 200 chars)")
    corrected_snippet: str = Field(..., max_length=200, description="Corrected text (first 200 chars)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="AI confidence score")
    explanation: str = Field(..., description="Explanation of the correction")


class CorrectionURLs(BaseModel):
    """Generated URLs for correction review.

    Attributes:
        original_markdown: URL to original markdown file
        corrected_markdown: URL to corrected markdown file
        page_images: List of URLs to page images
    """
    original_markdown: str
    corrected_markdown: str
    page_images: list[str]


class CorrectionReviewResponse(BaseModel):
    """Response for correction review endpoint.

    Provides all information needed for human review of text corrections.

    Attributes:
        job_id: Unique job identifier
        total_corrections: Total number of corrections made
        overall_confidence: Average confidence across all corrections
        by_type: Count of corrections grouped by type
        by_page: Count of corrections grouped by page
        corrections: List of correction summaries
        urls: Generated URLs for assets (original, corrected, page images)
        expires_at: UTC timestamp when approval link expires

    Example:
        >>> response = CorrectionReviewResponse(
        ...     job_id="abc-123",
        ...     total_corrections=5,
        ...     overall_confidence=0.89,
        ...     by_type={"heading_level": 2, "list_structure": 3},
        ...     by_page={1: 3, 2: 2},
        ...     corrections=[...],
        ...     urls={
        ...         "original_markdown": "https://s3.../original.md",
        ...         "corrected_markdown": "https://s3.../corrected.md",
        ...         "page_images": ["https://s3.../page-1.png"]
        ...     },
        ...     expires_at="2024-01-15T14:00:00Z"
        ... )
    """
    job_id: str
    total_corrections: int
    overall_confidence: float = Field(..., ge=0.0, le=1.0)
    by_type: dict[str, int]
    by_page: dict[int, int]
    corrections: list[CorrectionSummary]
    urls: CorrectionURLs
    expires_at: str


class CorrectionDecisionRequest(BaseModel):
    """Request for correction approval decision.

    Attributes:
        token: Correction approval token (validates permission for this job)
        decision: Binary approval decision ("approved" or "rejected")
        reviewed_by: Reviewer identifier (email or user ID)
        justification: Explanation for decision (10-1000 chars)

    Example:
        >>> decision = CorrectionDecisionRequest(
        ...     token="secure-token-xyz",
        ...     decision="approved",
        ...     justification="Text corrections improve readability and structure",
        ...     reviewed_by="instructor@uic.edu"
        ... )
    """
    token: str = Field(
        ...,
        min_length=10,
        description="Correction approval token"
    )
    decision: Literal["approved", "rejected"] = Field(
        ...,
        description="Approval or rejection of corrections"
    )
    reviewed_by: str = Field(
        ...,
        min_length=3,
        description="Reviewer email or user ID"
    )
    justification: str = Field(
        ...,
        min_length=10,
        max_length=1000,
        description="Explanation for decision"
    )


class CorrectionDecisionResponse(BaseModel):
    """Response for correction decision submission.

    Attributes:
        job_id: Job identifier
        status: Job status after decision (should be "completed")
        decision: Decision that was recorded
        message: Success message

    Example:
        >>> response = CorrectionDecisionResponse(
        ...     job_id="abc-123",
        ...     status="completed",
        ...     decision="approved",
        ...     message="Corrections approved successfully - corrected markdown is now final"
        ... )
    """
    job_id: str
    status: str
    decision: str
    message: str


@router.get(
    "/{job_id}/review",
    response_model=CorrectionReviewResponse,
    summary="Get correction details for review",
    description="Retrieve correction results and job details for human review interface"
)
async def get_correction_review(
    job_id: str,
    token: str = Query(..., description="Correction approval token"),
    correction_approval: CorrectionApprovalService = Depends(get_correction_approval_service),
    job_service: JobService = Depends(get_job_service),
    url_service: S3URLService = Depends(get_s3_url_service)
) -> CorrectionReviewResponse:
    """Get correction results and job details for review.

    Validates correction approval token and returns all information needed
    for human review of AI-generated text corrections.

    Token validates permission to view this specific job's corrections.

    Args:
        job_id: Job identifier from URL path
        token: Correction approval token (query parameter)
        correction_approval: Correction approval service (injected)
        job_service: Job service (injected)
        storage: Storage service (injected)

    Returns:
        CorrectionReviewResponse with correction details and generated S3 URLs

    Raises:
        HTTPException 401: Invalid token or token does not match job ID
        HTTPException 404: Job not found or no correction results found
        HTTPException 500: Server error

    Example:
        GET /api/corrections/abc-123/review?token=secure-token-xyz
        Returns:
        {
            "job_id": "abc-123",
            "total_corrections": 5,
            "overall_confidence": 0.89,
            "by_type": {"heading_level": 2, "list_structure": 3},
            "by_page": {1: 3, 2: 2},
            "corrections": [...],
            "urls": {
                "original_markdown": "https://s3.../original.md",
                "corrected_markdown": "https://s3.../corrected.md",
                "page_images": ["https://s3.../page-1.png"]
            },
            "expires_at": "2024-01-15T14:00:00Z"
        }
    """
    try:
        # Validate token maps to this job_id
        token_job_id = await correction_approval.validate_correction_approval_token(token)
        if token_job_id != job_id:
            raise HTTPException(
                status_code=401,
                detail="Token does not match job ID"
            )

        # Get full job data
        job = await job_service.get_job(job_id)
        if not job:
            raise HTTPException(
                status_code=404,
                detail="Job not found"
            )

        # Read correction_results from top-level field (NOT metadata)
        correction_results = job.get("correction_results", [])
        if not correction_results:
            raise HTTPException(
                status_code=404,
                detail="No correction results found for this job"
            )

        # Build correction summaries (with 200-char snippets)
        corrections = []
        by_type: dict[str, int] = {}
        by_page: dict[int, int] = {}
        total_confidence = 0.0
        total_corrections = 0

        for page_result in correction_results:
            page_num = page_result.get("page", 0)
            page_corrections = page_result.get("corrections", [])

            for correction in page_corrections:
                correction_type = correction.get("type", "other")
                original_text = correction.get("original", "")
                corrected_text = correction.get("corrected", "")
                confidence = correction.get("confidence", 0.0)
                explanation = correction.get("explanation", "")

                # Create snippet (first 200 chars)
                original_snippet = original_text[:200]
                corrected_snippet = corrected_text[:200]

                corrections.append(CorrectionSummary(
                    page=page_num,
                    type=correction_type,
                    original_snippet=original_snippet,
                    corrected_snippet=corrected_snippet,
                    confidence=confidence,
                    explanation=explanation
                ))

                # Update counters
                by_type[correction_type] = by_type.get(correction_type, 0) + 1
                by_page[page_num] = by_page.get(page_num, 0) + 1
                total_confidence += confidence
                total_corrections += 1

        # Calculate overall confidence
        overall_confidence = total_confidence / total_corrections if total_corrections > 0 else 0.0

        # Generate URLs from S3 keys (top-level fields)
        original_key = job.get("original_markdown_key")
        corrected_key = job.get("corrected_markdown_key")
        page_image_keys = job.get("page_image_keys", [])

        if not original_key or not corrected_key:
            logger.error(f"Missing markdown keys in job {job_id}")
            raise HTTPException(
                status_code=500,
                detail="Markdown keys not found in job data"
            )

        # Generate URLs on-demand from keys
        urls = CorrectionURLs(
            original_markdown=await url_service.generate_url(
                original_key,
                bucket=url_service.results_bucket
            ),
            corrected_markdown=await url_service.generate_url(
                corrected_key,
                bucket=url_service.results_bucket
            ),
            page_images=[
                await url_service.generate_url(key, bucket=url_service.temp_bucket)
                for key in page_image_keys
            ]
        )

        # Get expiration timestamp from top-level field
        expires_at = job.get("correction_expires_at", "")

        return CorrectionReviewResponse(
            job_id=job_id,
            total_corrections=total_corrections,
            overall_confidence=overall_confidence,
            by_type=by_type,
            by_page=by_page,
            corrections=corrections,
            urls=urls,
            expires_at=expires_at
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to retrieve correction review details: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve correction review details: {str(e)}"
        )


@router.patch(
    "/{job_id}",
    response_model=CorrectionDecisionResponse,
    summary="Submit correction approval decision",
    description="Submit approval or rejection decision for text corrections"
)
async def submit_correction_decision(
    job_id: str,
    decision_input: CorrectionDecisionRequest,
    correction_approval: CorrectionApprovalService = Depends(get_correction_approval_service),
    job_service: JobService = Depends(get_job_service)
) -> CorrectionDecisionResponse:
    """Submit correction approval or rejection decision.

    Validates correction approval token and processes decision:
    - Approved: Corrected markdown moved to final location
    - Rejected: Original markdown moved to final location

    Token in request body validates permission for this specific job.

    Args:
        job_id: Job identifier from URL path
        decision_input: Approval decision details (includes token)
        correction_approval: Correction approval service (injected)
        job_service: Job service (injected)

    Returns:
        CorrectionDecisionResponse with success message

    Raises:
        HTTPException 401: Invalid token or token does not match job ID
        HTTPException 404: Job not found
        HTTPException 500: Server error processing decision

    Example:
        PATCH /api/corrections/abc-123
        Body:
        {
            "token": "secure-token-xyz",
            "decision": "approved",
            "justification": "Text corrections improve readability",
            "reviewed_by": "instructor@uic.edu"
        }
        Returns:
        {
            "job_id": "abc-123",
            "status": "completed",
            "decision": "approved",
            "message": "Corrections approved successfully - corrected markdown is now final"
        }
    """
    try:
        # Validate token maps to this job_id
        token = decision_input.token
        token_job_id = await correction_approval.validate_correction_approval_token(token)
        if token_job_id != job_id:
            raise HTTPException(
                status_code=401,
                detail="Token does not match job ID"
            )

        # Build decision dict for service
        decision_dict = {
            "decision": decision_input.decision,
            "reviewed_by": decision_input.reviewed_by,
            "justification": decision_input.justification
        }

        # Process decision
        await correction_approval.process_correction_approval_decision(
            job_id=job_id,
            decision=decision_dict
        )

        # Return success response
        if decision_input.decision == "approved":
            message = "Corrections approved successfully - corrected markdown is now final"
        else:
            message = "Corrections rejected successfully - original markdown is now final"

        return CorrectionDecisionResponse(
            job_id=job_id,
            status="completed",
            decision=decision_input.decision,
            message=message
        )

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to process correction decision: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process correction decision: {str(e)}"
        )
