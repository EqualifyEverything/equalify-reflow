# PRD-027: Review Checklist API

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: Phase 4: Assemble (Review Workflow)
**Estimated Effort**: 2 days
**Dependencies**: PRD-021 (Data Models), PRD-026 (Assembly Service)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

The new architecture produces `ProcessingResult` with `ReviewChecklist` containing items that need human review. We need API endpoints to:

1. **Get processing result** - Full result with trace and checklist
2. **Get review checklist** - Just the checklist for review UI
3. **Submit review** - Human submits decision for a review item
4. **Apply reviews** - Apply all reviewed decisions to final markdown
5. **Get checklist summary** - Lightweight summary for list views

## Success Criteria

- [ ] ProcessingResult exposed via API
- [ ] ReviewChecklist accessible with groupings
- [ ] Review submission with multiple choice + custom input
- [ ] Reviews applied to generate final markdown
- [ ] Summary endpoint for list views

## API Endpoints

### Endpoint Overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/documents/{job_id}/result` | Get full processing result |
| GET | `/api/documents/{job_id}/checklist` | Get review checklist |
| GET | `/api/documents/{job_id}/checklist/summary` | Get lightweight summary |
| POST | `/api/documents/{job_id}/checklist/{item_id}/review` | Submit review for item |
| POST | `/api/documents/{job_id}/apply-reviews` | Apply all reviews |

## Technical Requirements

### API Implementation

```python
# src/api/review_checklist.py

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from src.dependencies import get_storage_service, get_job_service
from src.services.storage_service import StorageService
from src.services.job_service import JobService
from src.shared.models.processing_result import ProcessingResult
from src.shared.models.review_checklist import ReviewChecklist, ReviewItem


router = APIRouter(prefix="/api/documents", tags=["review"])


# --- Request/Response Models ---

class ReviewSubmission(BaseModel):
    """Human review submission for a checklist item."""
    selected_option_id: str | None = Field(
        default=None,
        description="ID of the selected option, or None for 'Other'"
    )
    custom_input: str | None = Field(
        default=None,
        description="Custom text if 'Other' selected"
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
    """Lightweight summary for list views."""
    job_id: str
    total_items: int
    completed_items: int
    categories: list[str]
    agents: list[str]
    estimated_review_time_minutes: int
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
    page: int | None = None,
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
    """
    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    checklist = result.review_checklist

    # Apply filters using pre-computed groupings
    # Category is derived from Observation at construction time (see PRD-021)
    if category or agent or page:
        # Start with all items, then intersect with each filter
        filtered_item_ids: set[str] | None = None

        if category:
            category_items = checklist.by_category.get(category, [])
            category_ids = {i.id for i in category_items}
            filtered_item_ids = category_ids if filtered_item_ids is None else filtered_item_ids & category_ids

        if agent:
            agent_items = checklist.by_agent.get(agent, [])
            agent_ids = {i.id for i in agent_items}
            filtered_item_ids = agent_ids if filtered_item_ids is None else filtered_item_ids & agent_ids

        if page:
            page_items = checklist.by_page.get(page, [])
            page_ids = {i.id for i in page_items}
            filtered_item_ids = page_ids if filtered_item_ids is None else filtered_item_ids & page_ids

        # Build filtered items list preserving order
        if filtered_item_ids is not None:
            filtered_items = [i for i in checklist.items if i.id in filtered_item_ids]

            # Return a lightweight filtered view (not a full rebuild)
            # The groupings remain from the original for reference
            return ReviewChecklist(
                items=filtered_items,
                summary=f"{len(filtered_items)} items (filtered)",
                by_category=checklist.by_category,  # Keep original groupings for reference
                by_agent=checklist.by_agent,
                by_page=checklist.by_page,
                total_items=len(filtered_items),
                critical_items=sum(1 for i in filtered_items if i.agent_confidence < 0.7),
                completed_items=sum(1 for i in filtered_items if i.reviewed_at),
            )

    return checklist


@router.get("/{job_id}/checklist/summary")
async def get_checklist_summary(
    job_id: str,
    storage: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
) -> ChecklistSummary:
    """Get lightweight checklist summary for list views."""
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    checklist = result.review_checklist

    # Estimate review time (30 seconds per item on average)
    estimated_time = max(1, len(checklist.items) // 2)

    return ChecklistSummary(
        job_id=job_id,
        total_items=checklist.total_items,
        completed_items=checklist.completed_items,
        categories=list(checklist.by_category.keys()),
        agents=list(checklist.by_agent.keys()),
        estimated_review_time_minutes=estimated_time,
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
        selected_option = next(o for o in item.options if o.id == request.selected_option_id)

    now = datetime.utcnow()

    # Update the item
    item.selected_option_id = request.selected_option_id
    item.custom_input = request.custom_input
    item.reviewed_by = request.reviewed_by
    item.reviewed_at = now

    # Close linked Observation (simplified 2-field lifecycle)
    # See PRD-021 for simplified lifecycle
    obs = _find_observation_in_result(result, item.observation_id)
    if obs:
        # Determine resolution based on selected action
        if selected_option and selected_option.action == "keep":
            # Human chose to keep original
            obs.close("kept_original")
        else:
            # Human chose to fix (replace or custom input)
            obs.close("fixed")

    # Update checklist stats
    result.review_checklist.completed_items = sum(
        1 for i in result.review_checklist.items if i.reviewed_at
    )

    # Save updated result
    await storage.save_processing_result(job_id, result)

    remaining = result.review_checklist.total_items - result.review_checklist.completed_items

    return ReviewSubmissionResponse(
        status="reviewed",
        item_id=item_id,
        remaining_items=remaining,
    )


def _find_observation_in_result(
    result: ProcessingResult,
    observation_id: str,
) -> "Observation | None":
    """Find observation by ID across all agent traces."""
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

    When force=true, unreviewed observations are marked with resolution_path="skipped".
    """
    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(status_code=404, detail="Processing result not found")

    # Check if all items reviewed
    unreviewed = [i for i in result.review_checklist.items if not i.reviewed_at]

    if unreviewed and not request.force:
        raise HTTPException(
            status_code=400,
            detail=f"{len(unreviewed)} items still need review. Use force=true to apply anyway."
        )

    # Close skipped observations (force=true case)
    if unreviewed and request.force:
        for item in unreviewed:
            obs = _find_observation_in_result(result, item.observation_id)
            if obs:
                obs.close("skipped")

    # Apply reviews to markdown
    markdown = result.markdown
    reviews_applied = 0

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
            # Find the observation to get the original text
            # This requires accessing the full observation
            # For now, we'll use a simpler approach with the context
            markdown = _apply_item_replacement(markdown, item, replacement)
            reviews_applied += 1

    # Update result
    result.markdown = markdown
    result.status = "completed"

    await storage.save_processing_result(job_id, result)

    # Upload final markdown to S3
    markdown_url = await storage.upload_final_markdown(job_id, markdown)

    # Update job status
    await job_service.update_job_status(
        job_id,
        status="completed",
        substatus="reviews_applied",
        markdown_url=markdown_url,
    )

    return ApplyReviewsResponse(
        status="completed",
        reviews_applied=reviews_applied,
        markdown_url=markdown_url,
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
    """
    import re
    import logging

    logger = logging.getLogger(__name__)
    search_text = item.search_text

    # Layer 1: Exact match (fastest)
    if search_text in markdown:
        logger.debug(f"Layer 1 match for item {item.id}")
        return markdown.replace(search_text, replacement, 1)

    # Layer 2: Whitespace-normalized match
    def normalize_ws(s: str) -> str:
        return re.sub(r'\s+', ' ', s.strip())

    normalized_search = normalize_ws(search_text)
    lines = markdown.split('\n')
    for i, line in enumerate(lines):
        if normalized_search in normalize_ws(line):
            # Build a flexible regex pattern for the original text
            pattern = re.escape(search_text).replace(r'\ ', r'\s+')
            match = re.search(pattern, line)
            if match:
                # Found via whitespace-normalized match
                lines[i] = line[:match.start()] + replacement + line[match.end():]
                logger.debug(f"Layer 2 match for item {item.id} on line {i}")
                return '\n'.join(lines)

    # Layer 3: Context-aware match (use item.context to disambiguate)
    # Find a unique substring from context that contains search_text
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
        f"search_text='{search_text[:50]}...', page={item.page_num}, agent={item.agent}. "
        f"Markdown unchanged. Consider LLM-based edit fallback in future."
    )
    return markdown
```

### Router Registration

```python
# src/api/__init__.py or src/main.py

from src.api.review_checklist import router as review_checklist_router

app.include_router(review_checklist_router)
```

### Storage Service Extensions

```python
# src/services/storage_service.py (additions)

class StorageService:
    # ... existing methods ...

    async def load_processing_result(self, job_id: str) -> ProcessingResult | None:
        """Load processing result from S3."""
        key = f"jobs/{job_id}/processing_result.json"
        try:
            response = await self.s3_client.get_object(
                Bucket=self.results_bucket,
                Key=key,
            )
            data = json.loads(await response["Body"].read())
            return ProcessingResult.model_validate(data)
        except self.s3_client.exceptions.NoSuchKey:
            return None

    async def save_processing_result(self, job_id: str, result: ProcessingResult) -> None:
        """Save processing result to S3."""
        key = f"jobs/{job_id}/processing_result.json"
        await self.s3_client.put_object(
            Bucket=self.results_bucket,
            Key=key,
            Body=result.model_dump_json(),
            ContentType="application/json",
        )

    async def upload_final_markdown(self, job_id: str, markdown: str) -> str:
        """Upload final reviewed markdown and return URL."""
        key = f"jobs/{job_id}/final.md"
        await self.s3_client.put_object(
            Bucket=self.results_bucket,
            Key=key,
            Body=markdown.encode("utf-8"),
            ContentType="text/markdown",
        )
        return self._generate_url(self.results_bucket, key)
```

## API Examples

### Get Processing Result

```bash
GET /api/documents/abc-123/result

Response:
{
  "job_id": "abc-123",
  "status": "needs_review",
  "markdown": "# Document...",
  "confidence": 0.87,
  "processing_trace": {...},
  "review_checklist": {
    "items": [...],
    "summary": "3 items need review: 1 figures, 2 typography",
    "total_items": 3,
    "completed_items": 0
  }
}
```

### Get Checklist with Filters

```bash
GET /api/documents/abc-123/checklist?agent=typography&category=ocr

Response:
{
  "items": [
    {
      "id": "ri-456",
      "agent": "typography",
      "category": "ocr",
      "question": "Is 'Exxon' a typo for 'Enzo'?",
      "options": [
        {"id": "fix", "label": "Yes, replace with 'Enzo'", ...},
        {"id": "keep", "label": "No, 'Exxon' is correct", ...}
      ],
      ...
    }
  ],
  "summary": "1 items need review: 1 typography",
  "total_items": 1
}
```

### Submit Review

```bash
POST /api/documents/abc-123/checklist/ri-456/review
Content-Type: application/json

{
  "selected_option_id": "fix",
  "reviewed_by": "user@example.com"
}

Response:
{
  "status": "reviewed",
  "item_id": "ri-456",
  "remaining_items": 2
}
```

### Submit Custom Review

```bash
POST /api/documents/abc-123/checklist/ri-789/review
Content-Type: application/json

{
  "selected_option_id": null,
  "custom_input": "A flowchart showing the data processing pipeline with 5 stages",
  "reviewed_by": "user@example.com"
}
```

### Apply Reviews

```bash
POST /api/documents/abc-123/apply-reviews
Content-Type: application/json

{}

Response:
{
  "status": "completed",
  "reviews_applied": 3,
  "markdown_url": "https://s3.../jobs/abc-123/final.md",
  "unreviewed_items": 0
}
```

## Acceptance Criteria

### Get Result Endpoint
- [ ] Returns full ProcessingResult
- [ ] Includes processing trace
- [ ] Includes review checklist
- [ ] 404 if not found

### Get Checklist Endpoint
- [ ] Returns ReviewChecklist
- [ ] Filters by category work
- [ ] Filters by agent work
- [ ] Filters by page work

### Submit Review Endpoint
- [ ] Validates option ID
- [ ] Accepts custom input
- [ ] Updates reviewed_at
- [ ] Updates completed_items
- [ ] 400 if already reviewed
- [ ] Closes linked Observation with resolution ("fixed" or "kept_original")

### Apply Reviews Endpoint
- [ ] Checks all items reviewed
- [ ] Force flag works
- [ ] Uses search_text for layered matching (exact → whitespace-normalized → context-aware)
- [ ] Applies replacements correctly
- [ ] Updates status to completed
- [ ] Returns markdown URL
- [ ] When force=true, closes skipped observations with resolution="skipped"

## Deliverables

### Files to Create
```
src/api/
├── review_checklist.py

tests/api/
├── test_review_checklist.py
```

### Files to Modify
```
src/main.py                 # Register router
src/services/storage_service.py  # Add processing result methods
src/dependencies.py         # Add dependencies if needed
```

## Definition of Done

- [ ] All endpoints implemented
- [ ] Request/response models defined
- [ ] Storage methods added
- [ ] Filters working
- [ ] Review submission working
- [ ] Apply reviews working
- [ ] Unit tests passing
- [ ] Integration tests passing
- [ ] API documentation generated
