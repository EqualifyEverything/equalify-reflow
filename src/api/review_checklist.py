"""Review Checklist API endpoints.

This module provides API endpoints for the human review workflow:
- Get full processing result with trace and checklist
- Get review checklist with optional filters
- Get lightweight checklist summary
- Submit review for individual items
- Apply all reviews to generate final markdown

See PRD-027 for full specification.
"""

import logging
import re
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..dependencies import get_job_service, get_storage_service
from ..services.job_service import JobService
from ..services.storage_service import StorageService
from ..shared.models.observation import Observation
from ..shared.models.processing_result import ProcessingResult
from ..shared.models.review_checklist import ReviewChecklist, ReviewItem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["review"])

# Constants
MAX_CUSTOM_INPUT_LENGTH = 5000  # Maximum chars for custom review input


# --- Request/Response Models ---


class ReviewSubmission(BaseModel):
    """Human review submission for a checklist item."""

    selected_option_id: str | None = Field(
        default=None,
        description="ID of the selected option, or None for 'Other'"
    )
    custom_input: str | None = Field(
        default=None,
        max_length=MAX_CUSTOM_INPUT_LENGTH,
        description="Custom text if 'Other' selected (max 5000 chars)"
    )
    reviewed_by: str = Field(
        ...,
        min_length=1,
        description="Identifier for the reviewer"
    )


class ReviewSubmissionResponse(BaseModel):
    """Response after submitting a review."""

    status: str
    item_id: str
    remaining_items: int


class ChecklistSummary(BaseModel):
    """Lightweight summary for list views.

    Provides key metrics to help users prioritize which documents to review.
    """

    job_id: str
    total_items: int
    completed_items: int
    categories: list[str]
    agents: list[str]
    status: str


class ApplyReviewsRequest(BaseModel):
    """Request to apply all reviewed items."""

    force: bool = Field(
        default=False,
        description="Apply even if some items not reviewed"
    )


class ApplyReviewsResponse(BaseModel):
    """Response after applying reviews."""

    status: str
    reviews_applied: int
    failed_replacements: int = Field(
        default=0,
        description="Number of replacements that failed to match"
    )
    markdown_url: str | None
    unreviewed_items: int


# --- Endpoints ---


@router.get("/{job_id}/result")
async def get_processing_result(
    job_id: str,
    storage: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
) -> ProcessingResult:
    """Get full processing result including trace and checklist.

    This endpoint returns the complete ProcessingResult with:
    - Final markdown (with auto-corrections applied)
    - Full processing trace (glass box)
    - Review checklist for human verification

    Args:
        job_id: The job identifier
        storage: StorageService (injected)
        job_service: JobService (injected)

    Returns:
        ProcessingResult with all processing details

    Raises:
        HTTPException: 404 if job or result not found
    """
    # Verify job exists
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Load processing result
    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(
            status_code=404,
            detail="Processing result not found. Job may still be processing."
        )

    return result


@router.get("/{job_id}/checklist")
async def get_review_checklist(
    job_id: str,
    category: str | None = None,
    agent: str | None = None,
    page: int | None = Query(default=None, ge=1, description="Page number (1-indexed)"),
    storage: StorageService = Depends(get_storage_service),
) -> ReviewChecklist:
    """Get review checklist for a job.

    Optional filters:
    - category: Filter by category (alt_text, ocr, formatting, table_accuracy)
    - agent: Filter by agent (figures, tables, typography)
    - page: Filter by page number

    NOTE: Category groupings are pre-computed at checklist construction time
    (derived from linked Observations). Use the by_category/by_agent/by_page
    dicts for efficient filtering.

    Args:
        job_id: The job identifier
        category: Optional category filter
        agent: Optional agent filter
        page: Optional page number filter
        storage: StorageService (injected)

    Returns:
        ReviewChecklist, optionally filtered

    Raises:
        HTTPException: 404 if processing result not found
    """
    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    checklist = result.review_checklist

    # Apply filters using pre-computed groupings
    if category or agent or page:
        # Start with all items, then intersect with each filter
        filtered_item_ids: set[str] | None = None

        if category:
            category_items = checklist.by_category.get(category, [])
            category_ids = {i.id for i in category_items}
            filtered_item_ids = (
                category_ids
                if filtered_item_ids is None
                else filtered_item_ids & category_ids
            )

        if agent:
            agent_items = checklist.by_agent.get(agent, [])
            agent_ids = {i.id for i in agent_items}
            filtered_item_ids = (
                agent_ids
                if filtered_item_ids is None
                else filtered_item_ids & agent_ids
            )

        if page:
            page_items = checklist.by_page.get(page, [])
            page_ids = {i.id for i in page_items}
            filtered_item_ids = (
                page_ids
                if filtered_item_ids is None
                else filtered_item_ids & page_ids
            )

        # Build filtered items list preserving order
        if filtered_item_ids is not None:
            filtered_items = [
                i for i in checklist.items if i.id in filtered_item_ids
            ]

            # Return a lightweight filtered view
            return ReviewChecklist(
                items=filtered_items,
                summary=f"{len(filtered_items)} items (filtered)",
                by_category=checklist.by_category,
                by_agent=checklist.by_agent,
                by_page=checklist.by_page,
                total_items=len(filtered_items),
                critical_items=sum(
                    1 for i in filtered_items if i.agent_confidence < 0.7
                ),
                completed_items=sum(
                    1 for i in filtered_items if i.reviewed_at
                ),
            )

    return checklist


@router.get("/{job_id}/checklist/summary")
async def get_checklist_summary(
    job_id: str,
    storage: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
) -> ChecklistSummary:
    """Get lightweight checklist summary for list views.

    Args:
        job_id: The job identifier
        storage: StorageService (injected)
        job_service: JobService (injected)

    Returns:
        ChecklistSummary with key metrics

    Raises:
        HTTPException: 404 if job or result not found
    """
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    checklist = result.review_checklist

    return ChecklistSummary(
        job_id=job_id,
        total_items=checklist.total_items,
        completed_items=checklist.completed_items,
        categories=list(checklist.by_category.keys()),
        agents=list(checklist.by_agent.keys()),
        status=result.status,
    )


@router.post("/{job_id}/checklist/{item_id}/review")
async def submit_review(
    job_id: str,
    item_id: str,
    request: ReviewSubmission,
    storage: StorageService = Depends(get_storage_service),
) -> ReviewSubmissionResponse:
    """Submit human review for a checklist item.

    The review can either:
    - Select a predefined option (selected_option_id)
    - Provide custom input (custom_input with selected_option_id=None)

    At least one of selected_option_id or custom_input must be provided.

    This also updates the linked Observation status per PRD-021 state machine.

    Args:
        job_id: The job identifier
        item_id: The review item identifier
        request: Review submission data
        storage: StorageService (injected)

    Returns:
        ReviewSubmissionResponse with status and remaining count

    Raises:
        HTTPException: 404 if result/item not found, 400 if validation fails
    """
    # Validate input
    if not request.selected_option_id and not request.custom_input:
        raise HTTPException(
            status_code=400,
            detail="Must provide either selected_option_id or custom_input"
        )

    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    # Find the item
    item = next(
        (i for i in result.review_checklist.items if i.id == item_id),
        None
    )
    if not item:
        raise HTTPException(status_code=404, detail="Review item not found")

    # Check if already reviewed
    if item.reviewed_at:
        raise HTTPException(
            status_code=400,
            detail="Item already reviewed"
        )

    # Validate selected option if provided
    selected_option = None
    if request.selected_option_id:
        valid_options = [o.id for o in item.options]
        if request.selected_option_id not in valid_options:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid option. Valid options: {valid_options}"
            )
        selected_option = next(
            o for o in item.options if o.id == request.selected_option_id
        )

    now = datetime.now(UTC)

    # Update the item
    item.selected_option_id = request.selected_option_id
    item.custom_input = request.custom_input
    item.reviewed_by = request.reviewed_by
    item.reviewed_at = now

    # Close linked Observation (simplified 2-field lifecycle)
    obs = _find_observation_in_result(result, item.observation_id)
    if obs:
        if obs.status == "open":
            try:
                # Determine resolution based on selected action
                if selected_option and selected_option.action == "keep":
                    obs.close("kept_original")
                else:
                    obs.close("fixed")
            except ValueError:
                # Already closed (race condition or data inconsistency)
                logger.warning(f"Observation {obs.id} already closed when submitting review")
        elif obs.status == "closed":
            logger.debug(f"Observation {obs.id} already closed, skipping")
    else:
        logger.warning(f"Observation {item.observation_id} not found for review item {item_id}")

    # Update checklist stats
    result.review_checklist.completed_items = sum(
        1 for i in result.review_checklist.items if i.reviewed_at
    )

    # Save updated result
    await storage.save_processing_result(job_id, result)

    remaining = (
        result.review_checklist.total_items
        - result.review_checklist.completed_items
    )

    logger.info(
        f"Job {job_id}: Review submitted for item {item_id} by {request.reviewed_by}"
    )

    return ReviewSubmissionResponse(
        status="reviewed",
        item_id=item_id,
        remaining_items=remaining,
    )


def _find_observation_in_result(
    result: ProcessingResult,
    observation_id: str,
) -> Observation | None:
    """Find observation by ID across all agent traces.

    Args:
        result: ProcessingResult to search
        observation_id: Observation ID to find

    Returns:
        Observation if found, None otherwise
    """
    for agent_trace in result.processing_trace.agents:
        for obs in agent_trace.observations:
            if obs.id == observation_id:
                return obs
    return None


@router.post("/{job_id}/apply-reviews")
async def apply_reviews(
    job_id: str,
    request: ApplyReviewsRequest = ApplyReviewsRequest(),
    storage: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
) -> ApplyReviewsResponse:
    """Apply all reviewed items to generate final markdown.

    By default, all items must be reviewed before applying.
    Use force=true to apply with unreviewed items (they will be skipped).

    When force=true, unreviewed observations are marked with resolution="skipped".

    Args:
        job_id: The job identifier
        request: ApplyReviewsRequest with force flag
        storage: StorageService (injected)
        job_service: JobService (injected)

    Returns:
        ApplyReviewsResponse with applied count and markdown URL

    Raises:
        HTTPException: 404 if result not found, 400 if items not reviewed or wrong state
    """
    # Verify job exists and is in correct state
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    current_status = job.get("status", "")
    if current_status == "completed":
        raise HTTPException(
            status_code=400,
            detail="Job already completed. Cannot apply reviews again."
        )

    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    # Check if all items reviewed
    unreviewed = [
        i for i in result.review_checklist.items if not i.reviewed_at
    ]

    if unreviewed and not request.force:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(unreviewed)} items still need review. "
                "Use force=true to apply anyway."
            )
        )

    # Close skipped observations (force=true case)
    if unreviewed and request.force:
        for item in unreviewed:
            obs = _find_observation_in_result(result, item.observation_id)
            if obs and obs.status == "open":
                try:
                    obs.close("skipped")
                except ValueError:
                    logger.warning(f"Observation {obs.id} already closed when skipping")

    # Apply reviews to markdown
    markdown = result.markdown
    reviews_applied = 0
    failed_replacements = 0

    for item in result.review_checklist.items:
        if not item.reviewed_at:
            continue  # Skip unreviewed

        # Determine what to apply
        replacement = None

        if item.custom_input:
            # Custom input provided
            replacement = item.custom_input
        elif item.selected_option_id:
            # Find the selected option
            option = next(
                (o for o in item.options if o.id == item.selected_option_id),
                None
            )
            if option and option.action == "replace" and option.replacement_text:
                replacement = option.replacement_text
            elif option and option.action == "keep":
                # Keep original - no replacement needed
                continue

        if replacement is not None:
            new_markdown = _apply_item_replacement(markdown, item, replacement)
            if new_markdown == markdown:
                # Replacement failed - markdown unchanged
                failed_replacements += 1
            else:
                markdown = new_markdown
                reviews_applied += 1

    # Update result
    result.markdown = markdown
    result.status = "completed"

    await storage.save_processing_result(job_id, result)

    # Upload final markdown to S3
    markdown_key = await storage.upload_final_markdown(job_id, markdown)

    # Update job status with markdown URL
    await job_service.update_job_status(
        job_id,
        status="completed",
        substatus="reviews_applied",
        markdown_url=markdown_key,
    )

    logger.info(
        f"Job {job_id}: Applied {reviews_applied} reviews, "
        f"{failed_replacements} failed, {len(unreviewed)} unreviewed"
    )

    return ApplyReviewsResponse(
        status="completed",
        reviews_applied=reviews_applied,
        failed_replacements=failed_replacements,
        markdown_url=markdown_key,
        unreviewed_items=len(unreviewed),
    )


def _apply_item_replacement(
    markdown: str,
    item: ReviewItem,
    replacement: str,
) -> str:
    """Apply a single review item replacement to markdown.

    Uses item.search_text for locating the text to replace.
    Implements layered matching for robustness:
    - Layer 1: Exact match (fastest)
    - Layer 2: Whitespace-normalized match
    - Layer 3: Context-aware match
    - Fallback: Log warning, return unchanged

    Args:
        markdown: Current markdown text
        item: ReviewItem being applied
        replacement: Text to replace with

    Returns:
        Updated markdown (or unchanged if no match found)
    """
    search_text = item.search_text

    # Layer 1: Exact match (fastest)
    if search_text in markdown:
        logger.debug(f"Layer 1 match for item {item.id}")
        return markdown.replace(search_text, replacement, 1)

    # Layer 2: Whitespace-normalized match
    def normalize_ws(text: str) -> str:
        """Normalize whitespace for comparison."""
        return re.sub(r"\s+", " ", text.strip())

    normalized_search = normalize_ws(search_text)
    lines = markdown.split("\n")
    for i, line in enumerate(lines):
        if normalized_search in normalize_ws(line):
            # Build a flexible regex pattern: escape special chars, then convert
            # escaped spaces (\ ) to flexible whitespace (\s+)
            pattern = re.escape(search_text).replace(r"\ ", r"\s+")
            match = re.search(pattern, line)
            if match:
                # Found via whitespace-normalized match
                lines[i] = line[: match.start()] + replacement + line[match.end():]
                logger.debug(f"Layer 2 match for item {item.id} on line {i}")
                return "\n".join(lines)

    # Layer 3: Context-aware match (use item.context to disambiguate)
    context_snippet = item.context[:100] if item.context else ""
    if context_snippet and context_snippet in markdown:
        # Found context - search within that region
        start_idx = markdown.find(context_snippet)
        end_idx = start_idx + len(context_snippet) + 200  # Extend a bit

        region = markdown[start_idx:end_idx]
        if search_text in region:
            new_region = region.replace(search_text, replacement, 1)
            logger.debug(f"Layer 3 match for item {item.id} via context")
            return markdown[:start_idx] + new_region + markdown[end_idx:]

    # Fallback: Return unchanged and log warning
    logger.warning(
        f"Failed to find search_text for review item {item.id}. "
        f"search_text='{search_text[:50]}...', page={item.page_num}, "
        f"agent={item.agent}. Markdown unchanged."
    )
    return markdown


__all__ = [
    "router",
    "ReviewSubmission",
    "ReviewSubmissionResponse",
    "ChecklistSummary",
    "ApplyReviewsRequest",
    "ApplyReviewsResponse",
]
