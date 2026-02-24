# PDF Classification & Unsupported Document Detection

## Problem

The pipeline currently processes any PDF without checking whether it's a document type we can handle well. Forms, scanned-only documents, encrypted PDFs, and other edge cases either produce poor output silently or fail deep in the pipeline after expensive LLM processing. We need an early detection mechanism that classifies the PDF and either rejects unsupported types with a clear error or attaches warnings for limited-support types.

## Design Decision: Where to Introduce Classification

### Option A: Before Docling (pypdfium2 pre-flight) — **Recommended for hard blockers**

Run a lightweight `pypdfium2` check on raw PDF bytes *before* Docling extraction. This is the right place for cheap, structural metadata that doesn't require any content analysis:

- **Form detection** — `pypdfium2.PdfDocument.get_formtype()` returns 0 (none), 1 (AcroForm), 2 (XFA)
- **Encryption** — pypdfium2 will fail to open password-protected PDFs
- **PDF version** — `get_version()` for compatibility checks
- **Producer/Creator metadata** — `get_metadata_dict()` reveals the tool that generated the PDF (scanner software, Word, LaTeX, etc.)
- **Tagged PDF** — `is_tagged()` tells us if accessibility tagging exists already
- **Page count** — sanity check before committing to extraction
- **Page dimensions** — detect unusual sizes (labels, envelopes, oversized posters)

**Why here:** These checks cost ~5ms on any PDF size. No reason to run a full Docling extraction just to reject a form PDF.

### Option B: After Docling extraction — **Recommended for content-based signals**

Some classifications require Docling's output to determine:

- **Scanned/image-only** — `chars_per_page < 50` (already computed, could be refined)
- **Text density anomalies** — near-empty pages, extremely sparse content
- **Figure-heavy documents** — `figure_count / total_pages` ratio
- **Layout type distribution** — already computed via `_detect_page_columns()`

**Why here:** These need the actual extraction to have run. But they execute before any LLM calls, so they're still cheap.

### Option C: During Structure Analysis (Phase 1) — **Not recommended as the primary check**

Phase 1 already detects `is_scanned`, `has_tables`, `has_equations`, etc. per-page via Claude vision. But:

- This costs LLM tokens (~$0.01-0.05 per page)
- If we're going to reject the document, we've already wasted time and money
- Phase 1 is the right place for *refinements* (e.g., "this page is a scan even though text was extractable") but not the right place for *gating*

### Recommendation: Two-layer approach

```
PDF bytes
  │
  ├─ Layer 1: Pre-flight (pypdfium2) ─── hard blockers → reject/error
  │
  ├─ Docling extraction (v0)
  │
  ├─ Layer 2: Post-extraction analysis ── soft warnings → attach to result
  │
  └─ Phase 1+ continues...
```

## PDF Types to Detect

### Hard Blockers (return error, don't process)

| Type | Detection Method | Layer | Rationale |
|------|-----------------|-------|-----------|
| **AcroForm PDF** | `form_type >= 1` AND widget annotation count > 0 | Pre-flight | Forms have interactive fields, checkboxes, dropdowns — Docling extracts none of this; output is meaningless. Note: many PDFs declare AcroForm without having real fields — we count actual widget annotations (subtype 20) to avoid false positives. |
| **XFA Form PDF** | `form_type in (2, 3)` AND widget annotation count > 0 | Pre-flight | XML-based dynamic forms (full XFA or hybrid AcroForm+XFA), completely opaque to layout extraction |
| **Password-protected** | pypdfium2 open fails / catches exception | Pre-flight | Can't extract anything |
| **Empty PDF** | 0 pages after extraction | Post-extraction | Nothing to process |
| **Oversized PDF** | Page count > configurable limit (e.g., 200) | Pre-flight | Resource protection; course materials shouldn't be 200+ pages |

### Soft Warnings (process with warning attached to result)

| Type | Detection Method | Layer | Warning Message |
|------|-----------------|-------|-----------------|
| **Scanned/image-only** | `chars_per_page < 50` (existing) + producer metadata contains scan-related keywords | Both | "Document appears to be a scan. OCR is not enabled; text extraction may be incomplete." |
| **Presentation slides** | Producer = PowerPoint/Keynote/Impress + low text density + high figure ratio | Post-extraction | "Document appears to be a slide deck. Layout-heavy content may not convert cleanly to linear markdown." |
| **Spreadsheet-as-PDF** | Producer = Excel/Calc + mostly tables + landscape orientation | Both | "Document appears to be an exported spreadsheet. Complex table structures may lose formatting." |
| **Very large pages** | Page dimensions significantly exceed standard sizes (A4/Letter) | Pre-flight | "Document contains oversized pages (e.g., posters). Content may be truncated or poorly laid out." |
| **Mixed orientation** | Some pages portrait, some landscape | Post-extraction | "Document contains mixed page orientations. Landscape pages may have layout issues." |
| **Nearly empty** | `chars_per_page < 200` but not zero | Post-extraction | "Document has very little extractable text per page." |

## Data Model

### New model: `PdfClassification`

```python
# src/services/pdf_classification.py

class PdfDocumentType(str, Enum):
    """High-level document type classification."""
    STANDARD = "standard"          # Normal text document
    FORM = "form"                  # AcroForm or XFA
    SCANNED = "scanned"            # Image-only, needs OCR
    PRESENTATION = "presentation"  # Slides
    SPREADSHEET = "spreadsheet"    # Table-heavy export
    UNKNOWN = "unknown"

class ClassificationSeverity(str, Enum):
    """Severity of a classification finding."""
    ERROR = "error"      # Hard block — don't process
    WARNING = "warning"  # Process with degraded confidence

class ClassificationFinding(BaseModel):
    """A single finding from PDF classification."""
    code: str            # Machine-readable: "form_detected", "scanned_document", etc.
    severity: ClassificationSeverity
    message: str         # Human-readable explanation
    details: dict = {}   # Additional metadata (e.g., form_type=2, producer="Adobe Scan")

class PdfClassification(BaseModel):
    """Complete classification result for a PDF."""
    document_type: PdfDocumentType
    findings: list[ClassificationFinding] = []
    metadata: PdfMetadata  # See below

    @property
    def has_errors(self) -> bool:
        return any(f.severity == ClassificationSeverity.ERROR for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == ClassificationSeverity.WARNING for f in self.findings)

class PdfMetadata(BaseModel):
    """Raw PDF metadata extracted during classification."""
    page_count: int
    pdf_version: str | None = None
    producer: str | None = None
    creator: str | None = None
    title: str | None = None
    is_tagged: bool = False
    form_type: int = 0                        # 0=none, 1=AcroForm, 2=XFA
    page_dimensions: list[tuple[float, float]] = []  # (width, height) per page
    is_encrypted: bool = False
```

### Surface warnings in `PipelineViewerResult`

```python
# Add to PipelineViewerResult
class PipelineViewerResult(BaseModel):
    ...
    classification: PdfClassification | None = None  # NEW
    warnings: list[str] = Field(default_factory=list)  # NEW — human-readable
```

### Surface in API response

```python
# Add to job state / API response schemas
class ProcessingResponse(JobStatusBase):
    ...
    warnings: list[str] = []  # NEW — propagated from classification
```

## Implementation Plan

### Phase 1: Pre-flight Classification Service

**New file:** `src/services/pdf_classifier.py`

```
classify_pdf(file_content: bytes) -> PdfClassification
```

1. Open PDF with pypdfium2 (catch errors → encrypted finding)
2. Extract metadata: version, producer, creator, tagged, form_type
3. Get page count and dimensions
4. Run hard-blocker checks (forms, encrypted, oversized)
5. Run soft-warning heuristics (producer-based scan detection, unusual dimensions)
6. Return `PdfClassification`

**Estimated cost:** <10ms per PDF, zero external dependencies beyond pypdfium2 (already installed via Docling).

### Phase 2: Post-Extraction Enrichment

After Docling runs in `_step_docling()`, enrich the classification with content-based signals:

1. Text density analysis (refine `is_likely_scanned` into the classification)
2. Figure-to-page ratio
3. Layout distribution (all presentation? all single-column?)
4. Mixed orientation detection from page images

### Phase 3: Integration Points

#### A. In `PipelineViewerService.process()`

```python
async def process(self, file_content, filename, ...) -> PipelineViewerResult:
    result = PipelineViewerResult(filename=filename, total_pages=0)

    # NEW: Pre-flight classification
    classification = classify_pdf(file_content)
    result.classification = classification

    if classification.has_errors:
        # Attach error step and return early
        result.steps.append(StepResult(
            name="classification",
            display_name="PDF Classification",
            version_after="v0",
            elapsed_ms=classification_elapsed,
            error="; ".join(f.message for f in classification.findings
                           if f.severity == ClassificationSeverity.ERROR),
        ))
        return result

    result.warnings = [f.message for f in classification.findings
                       if f.severity == ClassificationSeverity.WARNING]

    # Continue with Docling extraction...
    await self._step_docling(result, file_content, ...)

    # NEW: Post-extraction enrichment
    enrich_classification(result.classification, result)

    # ... rest of pipeline
```

#### B. In `DocumentProcessingService.process_document()`

```python
# After pipeline returns, check for hard errors
if result.classification and result.classification.has_errors:
    error_msg = "; ".join(
        f.message for f in result.classification.findings
        if f.severity == ClassificationSeverity.ERROR
    )
    await self._update_job_state(job_id, status="failed", error=error_msg)
    return result

# Store warnings in job state for API consumers
if result.warnings:
    await self._update_job_state(job_id, warnings=json.dumps(result.warnings))
```

#### C. In API response

Warnings propagated through job state → API response so the frontend can display them.

### Phase 4: Configuration

Add configurable thresholds to `src/config.py`:

```python
# PDF Classification
PDF_MAX_PAGES: int = 200
PDF_MIN_CHARS_PER_PAGE_SCANNED: int = 50
PDF_BLOCK_FORMS: bool = True
PDF_BLOCK_ENCRYPTED: bool = True
```

### Phase 5: Tests

- **Unit tests** for `pdf_classifier.py` — test each detection path with crafted/fixture PDFs
- **Unit tests** for enrichment logic — mock Docling output, verify classification is updated
- **Integration test** — submit a form PDF via API, verify error response
- **Integration test** — submit a scanned PDF, verify warning in response

## File Changes Summary

| File | Change |
|------|--------|
| `src/services/pdf_classifier.py` | **NEW** — classification service |
| `src/services/pipeline_viewer_models.py` | Add `PdfClassification`, `PdfMetadata`, `ClassificationFinding` models |
| `src/services/pipeline_viewer.py` | Call classifier pre-flight + post-extraction enrichment |
| `src/services/document_processing_service.py` | Handle classification errors, propagate warnings |
| `src/config.py` | Add classification thresholds |
| `src/shared/constants/` | Classification codes as constants |
| `src/api/schemas.py` | Add `warnings` to response schemas |
| `tests/unit/services/test_pdf_classifier.py` | **NEW** — unit tests |
| `tests/integration/test_classification.py` | **NEW** — integration tests |

## Open Questions

1. **Should classification block at the PII stage too?** We could run pre-flight classification during PII scanning (which already loads the PDF) to fail even faster. This would avoid the S3 round-trip for unsupported documents.

2. **Should warnings affect confidence scores?** A "presentation" classification could automatically lower the final confidence score, signaling that the output needs more review.

3. **Should we add a new job status?** e.g., `"unsupported"` as a terminal status distinct from `"failed"`, so the frontend can show a more specific message than generic failure.

4. **Scan detection + OCR:** Currently `do_ocr=False`. If we detect a scanned document, should we re-run Docling with `do_ocr=True` as a fallback, or just warn? (This would significantly increase processing time.)
