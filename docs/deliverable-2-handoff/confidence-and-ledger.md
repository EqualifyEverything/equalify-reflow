# Confidence Scoring & Ledger System

The system uses confidence scores to determine which edits to apply automatically versus flag for review. Every edit is tracked in a ledger for full transparency.

## Confidence Scoring

### How Confidence Works

Each specialized tool (subagent) returns a confidence score between 0.0 and 1.0 representing how certain the AI is about its output.

### Decision Thresholds

| Confidence | Action | Flag |
|------------|--------|------|
| ≥ 0.8 | Apply automatically | `needs_review: false` |
| 0.5 - 0.8 | Apply with review flag | `needs_review: true` |
| < 0.5 | Skip edit | Logged for manual attention |

### What Affects Confidence

| Higher Confidence | Lower Confidence |
|-------------------|------------------|
| Clear visual content, unambiguous context | Ambiguous content, missing context |
| Standard formatting, high OCR quality | Non-standard layouts, poor scan quality |

### Confidence by Task Type

| Task | Typical Range | Factors |
|------|---------------|---------|
| Alt-text (simple figures) | 0.85-0.95 | Image clarity, caption presence |
| Alt-text (complex charts) | 0.60-0.80 | Data density, legend clarity |
| Table transcription | 0.75-0.90 | Table complexity, merged cells |
| Heading fix | 0.85-0.95 | Clear hierarchy signals |
| Typography | 0.70-0.85 | Context-dependent semantics |
| Citation linking | 0.80-0.90 | Reference section presence |
| Footnote correction | 0.75-0.85 | Marker clarity |

## Document Confidence Score

The overall document confidence is calculated from:

1. **Page confidences:** Average of per-page scores
2. **Critical issue count:** Penalties for unresolved issues
3. **Recovery edits:** Impact on final quality

### Calculation

The document confidence score combines page-level scores with quality penalties:

1. **Base score:** Average of all page confidence scores
2. **Critical issue penalty:** Subtract min(0.30, critical_issues × 0.05)
3. **Recovery penalty:** Subtract min(0.10, recovery_edits × 0.02)
4. **Final:** Clamp result between 0.0 and 1.0

**Formula:**
```
final_score = base_avg - critical_penalty - recovery_penalty
final_score = max(0.0, min(1.0, final_score))
```

**Example:**
- Base average: 0.90
- Critical issues: 2 → penalty = 2 × 0.05 = 0.10
- Recovery edits: 3 → penalty = 3 × 0.02 = 0.06
- Final: 0.90 - 0.10 - 0.06 = 0.74

### Score Interpretation

| Score | Interpretation |
|-------|----------------|
| 0.90+ | High quality, minimal review needed |
| 0.75-0.90 | Good quality, spot-check recommended |
| 0.60-0.75 | Moderate quality, review flagged items |
| < 0.60 | Lower quality, thorough review recommended |

## The Ledger System

Every edit made by the pipeline is recorded in a ledger, providing a complete audit trail.

### Ledger Entry Structure

```json
{
  "entry_id": "edit_abc123",
  "page": 1,
  "action": "ALT_TEXT",
  "target": "figure_001",
  "before": "<!-- image placeholder -->",
  "after": "![Line graph showing temperature increase from 1900-2020](figure1.png)",
  "reasoning": "Generated descriptive alt-text for climate data visualization. Identified trend line, axis labels, and data source from surrounding context.",
  "confidence": 0.88,
  "timestamp": "2025-01-09T10:03:15.234Z",
  "needs_review": false
}
```

### Entry Fields

| Field | Description |
|-------|-------------|
| `entry_id` | Unique identifier for the edit |
| `job_id` | Job that created this entry |
| `page` | Page number where edit occurred |
| `action` | Type of edit (ALT_TEXT, TABLE_TRANSCRIPTION, etc.) |
| `target` | What was edited (figure ID, table ID, heading) |
| `before` | Original content |
| `after` | Modified content |
| `reasoning` | AI explanation for the change |
| `confidence` | Score from the subagent |
| `timestamp` | When the edit was committed |
| `validated` | Whether edit passed validation |
| `validation_feedback` | Feedback if validation failed |
| `needs_review` | Whether this requires human review |

### Action Types

| Action | Description |
|--------|-------------|
| `ALT_TEXT` | Generated or improved alt-text for image |
| `TABLE_TRANSCRIPTION` | Converted table image to markdown |
| `HEADING_FIX` | Adjusted heading level (H1→H2, etc.) |
| `OCR_FIX` | Corrected OCR errors |
| `TYPOGRAPHY` | Added semantic formatting |
| `CITATION_LINK` | Linked citation to reference |
| `FOOTNOTE_FIX` | Corrected footnote structure |
| `LIST_FIX` | Fixed list nesting or numbering |
| `ARTIFACT_REMOVAL` | Removed page break artifacts |
| `PARAGRAPH_MERGE` | Merged split paragraph |

## Ledger API

### Get Full Ledger

```bash
GET /api/v1/documents/{job_id}/ledger
```

Response groups entries by page:

```json
{
  "job_id": "abc123",
  "document_title": "document.pdf",
  "total_pages": 12,
  "pages_with_changes": 8,
  "total_edits": 45,
  "entries_needing_review": 3,
  "pages": [
    {
      "page": 1,
      "edit_count": 5,
      "entries": [...]
    }
  ],
  "final_markdown_url": "https://..."
}
```

### Filtering

The ledger response includes `entries_needing_review` count. Filter entries programmatically:

```javascript
const needsReview = ledger.pages.flatMap(p => p.entries).filter(e => e.needs_review);
const altTextEdits = ledger.pages.flatMap(p => p.entries).filter(e => e.action === 'ALT_TEXT');
```

## Review Workflow

### For Automatic Mode (`review_mode: auto`)

1. Processing completes immediately
2. Ledger available for optional review
3. Flagged items visible in ledger with `needs_review: true`

### For Human Review Mode (`review_mode: human`)

1. Processing pauses at review stage
2. Reviewer examines flagged items
3. Approves or requests corrections
4. Processing completes after approval

## Convergence Toward Accuracy

The confidence-based system creates a feedback loop:

1. **High confidence edits** apply automatically, freeing reviewer time
2. **Medium confidence edits** get human validation
3. **Low confidence situations** are logged for future improvement
4. **More compute** (additional verification passes) improves accuracy

The principle: *The more compute applied to semantic analysis, the closer the output converges to true document semantics.*

## Multi-Round Processing Models

Beyond single-pass processing, the system supports iterative refinement through multi-round processing. This section documents the data structures supporting that workflow.

### PageBoundaryMap

Maps line numbers in merged markdown back to source pages, enabling pageless processing while retaining ability to reference source pages for visual context.

**Fields:**
- `document_id` - Document identifier
- `boundaries` - List of PageBoundary objects (page_num, start_line, end_line)
- `total_lines` - Total lines in merged document

**Methods:**
- `get_page_for_line(line)` - Get the page number containing a specific line
- `get_pages_for_range(start, end)` - Get all pages overlapping a line range

### CriticReport

Output from CriticAgent analyzing merged markdown for quality assessment and issue detection.

**Fields:**
- `document_id` - Document identifier
- `round_number` - Which round of processing (1-indexed)
- `issues` - List of CriticIssue objects found
- `critical_count` - Number of critical severity issues
- `major_count` - Number of major severity issues
- `overall_quality` - Quality score (0.0-1.0)
- `ready_for_output` - Whether critic marks document ready for output
- `summary` - Human-readable summary of findings
- `analysis_duration_ms` - Time spent analyzing
- `input_tokens` / `output_tokens` - LLM usage

### RoundLoopResult

Final result of multi-round processing, containing converged markdown and complete history.

**Fields:**
- `document_id` - Document identifier
- `final_markdown` - Final corrected markdown
- `final_quality` - Quality score after all rounds
- `rounds_completed` - Number of rounds executed
- `convergence_reason` - Enum reason for stopping
- `round_contexts` - History of each round
- `total_edits` - Total edits across all rounds
- `total_duration_ms` / token usage - Performance metrics

### ConvergenceReason

Enum explaining why multi-round processing stopped:

- `MAX_ROUNDS_REACHED` - Hit the maximum rounds limit
- `QUALITY_THRESHOLD_MET` - Quality ≥ 0.85 with no critical issues
- `NO_IMPROVEMENT` - Quality didn't improve from previous round
- `NO_ISSUES_FOUND` - CriticAgent found no issues to address
- `CRITIC_MARKED_READY` - CriticAgent explicitly marked document as ready
- `ERROR` - Processing error occurred during a round

## Best Practices

| Role | Guidance |
|------|----------|
| Reviewers | Focus on `needs_review: true` entries, verify complex content (tables, charts, equations), trust 0.9+ scores |
| System Tuning | Monitor confidence distributions across document types, review skipped edits for extraction issues |
