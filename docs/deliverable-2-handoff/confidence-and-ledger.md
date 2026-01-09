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

```
document_confidence = (
    page_confidence_avg * 0.7 +
    (1 - critical_issues / total_elements) * 0.2 +
    recovery_success_rate * 0.1
)
```

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
| `page` | Page number where edit occurred |
| `action` | Type of edit (ALT_TEXT, TABLE_TRANSCRIPTION, etc.) |
| `target` | What was edited (figure ID, table ID, heading) |
| `before` | Original content |
| `after` | Modified content |
| `reasoning` | AI explanation for the change |
| `confidence` | Score from the subagent |
| `timestamp` | When the edit was committed |
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

## Best Practices

| Role | Guidance |
|------|----------|
| Reviewers | Focus on `needs_review: true` entries, verify complex content (tables, charts, equations), trust 0.9+ scores |
| System Tuning | Monitor confidence distributions across document types, review skipped edits for extraction issues |
