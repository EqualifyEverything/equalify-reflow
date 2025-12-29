# PRD-028: Review API v2 (Simplified)

## Overview

**Epic**: Phase 5 - Architecture Refactor
**Estimated Effort**: 1 day
**Dependencies**: PRD-021 (Data Models), PRD-027 (supersedes)
**Status**: Proposed

## Problem Statement

PRD-027 defined 5 endpoints for the review workflow:

| Endpoint | Purpose |
|----------|---------|
| `GET /result` | Full processing result |
| `GET /checklist` | Filtered checklist view |
| `GET /checklist/summary` | Lightweight stats |
| `POST /checklist/{item_id}/review` | Submit one review |
| `POST /apply-reviews` | Apply all reviews |

**Issues identified:**

1. **Redundant endpoints**: `/checklist` and `/checklist/summary` duplicate data already in `/result`
2. **N+1 API calls**: Reviewing N items requires N individual POST requests
3. **Mixed concerns**: The `force` flag on apply-reviews conflated "save progress" with "finalize"
4. **No save-progress workflow**: Staff need to review partially, save, and return later

## Solution: 3 Simple Endpoints

```
GET  /api/documents/{job_id}/result    # Read everything
PUT  /api/documents/{job_id}/reviews   # Save progress (idempotent)
POST /api/documents/{job_id}/apply     # Finalize (one-time action)
```

Each endpoint does exactly one thing. Standard REST semantics.

## API Specification

### 1. GET /result (unchanged)

Returns the full `ProcessingResult` including markdown, processing trace, and review checklist.

```
GET /api/documents/{job_id}/result

Response: ProcessingResult
{
  "job_id": "abc-123",
  "status": "needs_review",
  "markdown": "# Document...",
  "confidence": 0.87,
  "processing_trace": {...},
  "review_checklist": {
    "items": [...],
    "summary": "7 items need review",
    "total_items": 7,
    "completed_items": 0
  }
}
```

### 2. PUT /reviews (new)

Save review decisions. Full state replacement (client sends all decisions). Idempotent - can call multiple times to update.

```
PUT /api/documents/{job_id}/reviews
Content-Type: application/json

{
  "reviews": [
    {
      "item_id": "abc-123",
      "selected_option_id": "accept"
    },
    {
      "item_id": "def-456",
      "selected_option_id": "keep"
    },
    {
      "item_id": "ghi-789",
      "selected_option_id": null,
      "custom_input": "A flowchart showing 5 processing stages"
    }
  ],
  "reviewed_by": "staff@uic.edu"
}

Response:
{
  "status": "saved",
  "reviewed_count": 3,
  "total_items": 7,
  "remaining_items": 4
}
```

**Behavior:**
- Replaces all existing review decisions with the provided list
- Items not in the list are marked as unreviewed
- Can be called multiple times (idempotent)
- Job status stays `needs_review` until apply is called
- Returns 400 if job already `completed`

**Validation:**
- `item_id` must exist in checklist
- `selected_option_id` must be valid option for that item (or null for custom)
- If `selected_option_id` is null, `custom_input` is required

### 3. POST /apply (new)

Finalize and apply reviews to generate final markdown. One-time action.

```
POST /api/documents/{job_id}/apply
Content-Type: application/json

{
  "reviewed_by": "staff@uic.edu",
  "force": false
}

Response:
{
  "status": "completed",
  "reviews_applied": 7,
  "skipped_items": 0,
  "markdown_url": "https://s3.../jobs/abc-123/final.md"
}
```

**Behavior:**
- Applies all saved review decisions to the markdown
- Updates job status to `completed`
- Uploads final markdown to S3
- Returns 400 if:
  - Job already `completed`
  - Not all items reviewed (unless `force: true`)

**Force flag:**
- `force: false` (default): All items must be reviewed
- `force: true`: Apply anyway, unreviewed items are skipped

## Workflow Examples

### Happy Path: Review All Items

```
1. GET /result
   → See 7 items need review

2. PUT /reviews
   → Save decisions for 7 items
   → Response: { reviewed_count: 7, remaining_items: 0 }

3. POST /apply
   → Finalize
   → Response: { status: "completed", markdown_url: "..." }
```

### Save Progress: Multiple Sessions

```
Session 1:
  GET /result → 7 items
  PUT /reviews → save 3 decisions
  → Response: { reviewed_count: 3, remaining_items: 4 }
  (close browser)

Session 2:
  GET /result → see 3 reviewed, 4 remaining
  PUT /reviews → save all 7 decisions (including previous 3)
  → Response: { reviewed_count: 7, remaining_items: 0 }
  POST /apply → finalize
```

### Change Mind Before Finalizing

```
1. PUT /reviews → save decisions
2. GET /result → review what you saved
3. PUT /reviews → save updated decisions (replaces previous)
4. POST /apply → finalize with latest decisions
```

### Force Apply (Skip Unreviewed)

```
1. PUT /reviews → save 5 of 7 decisions
2. POST /apply { force: false }
   → 400 Error: "2 items not reviewed"
3. POST /apply { force: true }
   → 200: { reviews_applied: 5, skipped_items: 2 }
```

## Migration from PRD-027

| Old Endpoint | New Endpoint | Notes |
|--------------|--------------|-------|
| `GET /result` | `GET /result` | Unchanged |
| `GET /checklist` | Remove | Use `/result` |
| `GET /checklist/summary` | Remove | Use `/result` |
| `POST /checklist/{item_id}/review` | `PUT /reviews` | Batch, full state |
| `POST /apply-reviews` | `POST /apply` | Simplified |

**Breaking changes:**
- Individual review submission removed
- Clients must send full state on PUT /reviews

## Technical Implementation

### Request/Response Models

```python
class ReviewDecision(BaseModel):
    """Single review decision."""
    item_id: str
    selected_option_id: str | None = None
    custom_input: str | None = None

    @model_validator(mode='after')
    def validate_selection(self):
        if not self.selected_option_id and not self.custom_input:
            raise ValueError("Must provide selected_option_id or custom_input")
        return self


class SaveReviewsRequest(BaseModel):
    """Request to save review decisions."""
    reviews: list[ReviewDecision]
    reviewed_by: str = Field(..., min_length=1)


class SaveReviewsResponse(BaseModel):
    """Response after saving reviews."""
    status: Literal["saved"] = "saved"
    reviewed_count: int
    total_items: int
    remaining_items: int


class ApplyRequest(BaseModel):
    """Request to finalize and apply reviews."""
    reviewed_by: str = Field(..., min_length=1)
    force: bool = False


class ApplyResponse(BaseModel):
    """Response after applying reviews."""
    status: Literal["completed"] = "completed"
    reviews_applied: int
    skipped_items: int
    markdown_url: str
```

### Endpoint Implementation

```python
@router.put("/{job_id}/reviews")
async def save_reviews(
    job_id: str,
    request: SaveReviewsRequest,
    storage: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
) -> SaveReviewsResponse:
    """Save review decisions. Idempotent - can call multiple times."""

    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") == "completed":
        raise HTTPException(400, "Job already completed")

    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(404, "Processing result not found")

    # Validate all item_ids exist
    valid_ids = {item.id for item in result.review_checklist.items}
    for review in request.reviews:
        if review.item_id not in valid_ids:
            raise HTTPException(400, f"Invalid item_id: {review.item_id}")

    # Clear existing reviews and apply new ones
    reviewed_ids = {r.item_id for r in request.reviews}
    now = datetime.now(UTC)

    for item in result.review_checklist.items:
        if item.id in reviewed_ids:
            review = next(r for r in request.reviews if r.item_id == item.id)
            # Validate option if provided
            if review.selected_option_id:
                valid_options = [o.id for o in item.options]
                if review.selected_option_id not in valid_options:
                    raise HTTPException(400, f"Invalid option for item {item.id}")

            item.selected_option_id = review.selected_option_id
            item.custom_input = review.custom_input
            item.reviewed_by = request.reviewed_by
            item.reviewed_at = now
        else:
            # Clear any previous review
            item.selected_option_id = None
            item.custom_input = None
            item.reviewed_by = None
            item.reviewed_at = None

    # Update stats
    result.review_checklist.completed_items = len(reviewed_ids)

    await storage.save_processing_result(job_id, result)

    return SaveReviewsResponse(
        reviewed_count=len(reviewed_ids),
        total_items=result.review_checklist.total_items,
        remaining_items=result.review_checklist.total_items - len(reviewed_ids),
    )


@router.post("/{job_id}/apply")
async def apply_reviews(
    job_id: str,
    request: ApplyRequest,
    storage: StorageService = Depends(get_storage_service),
    job_service: JobService = Depends(get_job_service),
) -> ApplyResponse:
    """Finalize and apply reviews to generate final markdown."""

    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.get("status") == "completed":
        raise HTTPException(400, "Job already completed")

    result = await storage.load_processing_result(job_id)
    if not result:
        raise HTTPException(404, "Processing result not found")

    # Check completion
    unreviewed = [i for i in result.review_checklist.items if not i.reviewed_at]
    if unreviewed and not request.force:
        raise HTTPException(
            400,
            f"{len(unreviewed)} items not reviewed. Use force=true to skip."
        )

    # Apply reviews to markdown
    markdown = result.markdown
    applied = 0
    skipped = 0

    for item in result.review_checklist.items:
        if not item.reviewed_at:
            skipped += 1
            continue

        replacement = None
        if item.custom_input:
            replacement = item.custom_input
        elif item.selected_option_id:
            option = next(
                (o for o in item.options if o.id == item.selected_option_id),
                None
            )
            if option and option.action == "replace":
                replacement = option.replacement_text
            elif option and option.action == "keep":
                continue  # No change needed

        if replacement:
            markdown = apply_replacement(markdown, item, replacement)
            applied += 1

    # Save final result
    result.markdown = markdown
    result.status = "completed"
    await storage.save_processing_result(job_id, result)

    # Upload to S3
    markdown_url = await storage.upload_final_markdown(job_id, markdown)

    # Update job status
    await job_service.update_job_status(
        job_id,
        status="completed",
        markdown_url=markdown_url,
    )

    return ApplyResponse(
        reviews_applied=applied,
        skipped_items=skipped,
        markdown_url=markdown_url,
    )
```

## E2E Command Updates

The Claude Code E2E demo command (`.claude/commands/e2e/run.md`) must be updated to use the new API.

### Current Flow (N+1 API calls)

```bash
# Phase 6: Review - N individual POSTs
for each item in checklist:
    POST /api/documents/{job_id}/checklist/{item_id}/review

# Phase 6: Apply
POST /api/documents/{job_id}/apply-reviews
```

### New Flow (2 API calls)

```bash
# Phase 6: Review - collect all decisions, single PUT
PUT /api/documents/{job_id}/reviews
{
  "reviews": [
    { "item_id": "abc", "selected_option_id": "accept" },
    { "item_id": "def", "selected_option_id": "keep" },
    ...
  ],
  "reviewed_by": "e2e-demo@cli"
}

# Phase 6: Apply - single POST
POST /api/documents/{job_id}/apply
{
  "reviewed_by": "e2e-demo@cli",
  "force": false
}
```

### Changes to `.claude/commands/e2e/run.md`

| Section | Change |
|---------|--------|
| Phase 6 title | "Review Checklist" → "Review & Apply" |
| Interactive review | Still ask user about each item, but collect all decisions before submitting |
| Submit reviews | Replace loop of individual POSTs with single `PUT /reviews` |
| Apply reviews | Change `POST /apply-reviews` to `POST /apply` |
| Force flag | Same behavior, just on new endpoint |

### Example Update

**Before:**
```bash
# Submit each review
curl -X POST ".../checklist/{item_id}/review" -d '{"selected_option_id":"accept",...}'
# ... repeat for each item ...

# Apply
curl -X POST ".../apply-reviews" -d '{}'
```

**After:**
```bash
# Submit all reviews at once
curl -X PUT ".../reviews" -d '{"reviews":[...all decisions...],"reviewed_by":"..."}'

# Apply
curl -X POST ".../apply" -d '{"reviewed_by":"...","force":false}'
```

## Acceptance Criteria

### API Endpoints
- [ ] `GET /result` returns full ProcessingResult (unchanged)
- [ ] `PUT /reviews` saves all decisions in single request
- [ ] `PUT /reviews` is idempotent (can call multiple times)
- [ ] `PUT /reviews` validates item_ids and option_ids
- [ ] `PUT /reviews` returns 400 if job already completed
- [ ] `POST /apply` applies reviews to markdown
- [ ] `POST /apply` returns 400 if items unreviewed (unless force=true)
- [ ] `POST /apply` returns 400 if job already completed
- [ ] `POST /apply` uploads final markdown to S3
- [ ] Old endpoints removed: `/checklist`, `/checklist/summary`, `/checklist/{item_id}/review`, `/apply-reviews`

### E2E Command
- [ ] `.claude/commands/e2e/run.md` updated to use `PUT /reviews`
- [ ] `.claude/commands/e2e/run.md` updated to use `POST /apply`
- [ ] E2E demo runs successfully with new API

## Benefits

1. **Fewer endpoints**: 5 → 3 (40% reduction)
2. **Fewer API calls**: N+1 → 2 (one PUT, one POST)
3. **Clear semantics**: GET=read, PUT=save, POST=action
4. **Save progress**: PUT multiple times before POST
5. **Full state**: No server-side merge logic, client owns state
6. **Explicit finalize**: No magic auto-apply behavior

## Open Questions

1. **Should PUT /reviews return the updated checklist?** Currently returns counts only. Could return full checklist for convenience, but increases payload size.

2. **Versioning/conflict detection?** If two users review same job, last write wins. Could add `version` or `etag` for optimistic locking if needed later.
