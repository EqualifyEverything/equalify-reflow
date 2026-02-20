# Human Feedback System

The feedback system adds iterative human review to the pipeline viewer. It follows a **GitHub PR review** pattern: submit feedback, review proposed changes, iterate or approve.

## Architecture

### Session Lifecycle

```
Pipeline SSE stream completes
    │
    ├─ SessionStore.create(result, structure, section_map)
    │   └─ Returns session_id in SSE "done" event
    │
    ▼
POST /feedback (submit edits + comments)
    │
    ├─ EDIT items: selector + new_text → direct str_replace, no LLM
    │
    ├─ COMMENT items: description → decompose_feedback() → run_revision()
    │   └─ LLM operates on a DEEP COPY (no side effects)
    │
    └─ Returns CandidateChange[] for review
    │
    ▼
POST /review (accept/reject per change)
    │
    ├─ Accepted → str_replace on real result → new version (v4, v5, ...)
    ├─ Rejected + comment → saved for next feedback round
    │
    └─ action: "request_changes" → loop back to /feedback
         action: "approve" → finalize session
    │
    ▼
POST /approve (finalize, no more rounds)
```

### Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `session_store.py` | `src/services/` | In-memory session store with 1hr TTL |
| `pipeline_feedback.py` | `src/api/` | REST endpoints for feedback workflow |
| `pipeline_viewer.py` | `src/services/` | Service methods (resolve_selector, apply_direct_edits, etc.) |
| `pipeline_viewer_models.py` | `src/services/` | Data models (FeedbackItem, CandidateChange, etc.) |
| `revision.py` | `src/agents/prompts/` | Prompt module for decomposition + revision agents |

### Data Models

**FeedbackItem** — A single piece of human feedback:
- `type`: `edit` (direct replacement) or `comment` (LLM handles)
- `selector`: TextSelector with `exact`, optional `prefix`/`suffix`
- `new_text`: Replacement text (edits only)
- `description`: What's wrong / rationale

**CandidateChange** — A proposed change awaiting review:
- `change`: DocumentChange (page, old_text, new_text, reasoning)
- `source_type`: `direct_edit` or `ai_revision`
- `source_feedback_id`: Links back to originating FeedbackItem

**ChangeReview** — Human decision on a candidate:
- `decision`: `accept` or `reject`
- `comment`: Optional (rejection comments feed next round)

### Session Store

In-memory singleton at `src/services/session_store.py`. Each `PipelineSession` holds:
- `result`: PipelineViewerResult (mutated as changes are applied)
- `structure`: StructureResult (for outline context)
- `section_map`: SectionMap (for section context)
- `feedback_history`: All prior feedback rounds
- `candidate_changes`: Current pending candidates
- `revision_round`: Counter for feedback iterations
- `finalized`: Lock flag

Sessions expire after 1 hour of inactivity. Appropriate for the dev tool; production would use Redis.

## API Endpoints

Base: `/api/v1/pipeline/sessions`

### POST `/{session_id}/feedback`

Submit a batch of feedback items. Returns candidate changes.

```json
// Request
{
  "items": [
    {
      "id": "uuid1",
      "type": "edit",
      "selector": {"exact": "teh", "prefix": "fix ", "suffix": " typo"},
      "new_text": "the",
      "description": "Fix typo"
    },
    {
      "id": "uuid2",
      "type": "comment",
      "page": 3,
      "description": "The table headers are wrong",
      "feedback_type": "content"
    }
  ]
}

// Response
{
  "candidates": [...],
  "revision_round": 1
}
```

### POST `/{session_id}/review`

Accept/reject individual candidate changes.

```json
// Request
{
  "reviews": [
    {"change_id": "c1", "decision": "accept"},
    {"change_id": "c2", "decision": "reject", "comment": "Too aggressive"}
  ],
  "action": "request_changes"  // or "approve"
}

// Response
{
  "applied_count": 1,
  "rejected_count": 1,
  "new_version": "v4",
  "rejection_comments": ["Too aggressive"],
  "finalized": false
}
```

### POST `/{session_id}/approve`

Finalize session. No more feedback rounds.

### GET `/{session_id}/state`

Returns current versions, latest markdown, pending candidate count, feedback round count.

## TextSelector Resolution

Three-tier strategy to find text in the document:

1. **Prefix + exact + suffix** — Most precise, disambiguates repeated text
2. **Exact match alone** — Falls back when prefix/suffix don't match
3. **Fuzzy fallback** — Uses `_fuzzy_find_line()` for approximate single-line matches

## Design Decisions

**LLM on deep copy**: Comment feedback runs decomposition + revision agents on a `deepcopy()` of the result. The actual result is only mutated when changes are explicitly accepted. This eliminates rollback logic.

**Two feedback types**: EDITs are deterministic (zero LLM cost). COMMENTs reuse the existing decomposition + revision pipeline that was already built.

**Backward compatible**: The SSE `done` event gains a `session_id` field. Existing clients that don't consume it are unaffected.

**Batched reviews**: All feedback items in a round are submitted together, producing a combined set of candidates. This enables the LLM to consider all feedback holistically.
