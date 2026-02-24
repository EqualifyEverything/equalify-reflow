"""PDF classification service — pre-flight checks and content-based enrichment.

Runs lightweight checks on raw PDF bytes to detect unsupported document types
(forms, encrypted, oversized) before expensive Docling extraction, and enriches
classification with content-based signals after extraction.

Usage:
    classification = classify_pdf(file_content)
    if classification.has_errors:
        # reject document
    # ... run Docling ...
    enrich_classification(classification, pipeline_result)
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from io import BytesIO

from pydantic import BaseModel, Field

from ..config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Producer/creator strings that suggest scanner software
_SCANNER_PRODUCERS = frozenset({
    "adobe scan",
    "camscanner",
    "scanbot",
    "genius scan",
    "microsoft lens",
    "office lens",
    "epson scan",
    "hp scan",
    "brother scan",
    "fujitsu scansnap",
    "vuescan",
    "naps2",
    "readiris",
    "abbyy finereader",
    "tesseract",
    "ocrmypdf",
})

# Producer strings suggesting presentation software
_PRESENTATION_PRODUCERS = frozenset({
    "microsoft powerpoint",
    "impress",
    "keynote",
    "google slides",
})

# Producer strings suggesting spreadsheet software
_SPREADSHEET_PRODUCERS = frozenset({
    "microsoft excel",
    "calc",
    "google sheets",
})

# Classification finding codes
FINDING_FORM_ACROFORM = "form_acroform"
FINDING_FORM_XFA = "form_xfa"
FINDING_ENCRYPTED = "encrypted"
FINDING_EMPTY = "empty_document"
FINDING_TOO_MANY_PAGES = "too_many_pages"
FINDING_SCANNED = "scanned_document"
FINDING_SCAN_PRODUCER = "scan_producer"
FINDING_PRESENTATION = "presentation_document"
FINDING_SPREADSHEET = "spreadsheet_document"
FINDING_OVERSIZED_PAGES = "oversized_pages"
FINDING_LOW_TEXT_DENSITY = "low_text_density"
FINDING_MIXED_ORIENTATION = "mixed_orientation"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class PdfDocumentType(str, Enum):
    """High-level document type classification."""

    STANDARD = "standard"
    FORM = "form"
    SCANNED = "scanned"
    PRESENTATION = "presentation"
    SPREADSHEET = "spreadsheet"
    UNKNOWN = "unknown"


class ClassificationSeverity(str, Enum):
    """Severity of a classification finding."""

    ERROR = "error"
    WARNING = "warning"


class ClassificationFinding(BaseModel):
    """A single finding from PDF classification."""

    code: str
    """Machine-readable finding code (e.g. 'form_detected')."""

    severity: ClassificationSeverity
    """Whether this blocks processing or just warns."""

    message: str
    """Human-readable explanation."""

    details: dict = Field(default_factory=dict)
    """Additional metadata (e.g. form_type=2, producer='Adobe Scan')."""


class PdfMetadata(BaseModel):
    """Raw PDF metadata extracted during classification."""

    page_count: int = 0
    pdf_version: str | None = None
    producer: str | None = None
    creator: str | None = None
    title: str | None = None
    is_tagged: bool = False
    form_type: int = 0
    """0=none, 1=AcroForm, 2=XFA full, 3=XFA foreground (hybrid)."""

    form_field_count: int = 0
    """Number of actual interactive form field widgets found across all pages."""

    page_dimensions: list[tuple[float, float]] = Field(default_factory=list)
    """(width, height) per page in PDF points (1 point = 1/72 inch)."""

    is_encrypted: bool = False


class PdfClassification(BaseModel):
    """Complete classification result for a PDF."""

    document_type: PdfDocumentType = PdfDocumentType.UNKNOWN
    findings: list[ClassificationFinding] = Field(default_factory=list)
    metadata: PdfMetadata = Field(default_factory=PdfMetadata)
    elapsed_ms: int = 0

    @property
    def has_errors(self) -> bool:
        """True if any findings are hard blockers."""
        return any(f.severity == ClassificationSeverity.ERROR for f in self.findings)

    @property
    def has_warnings(self) -> bool:
        """True if any findings are soft warnings."""
        return any(f.severity == ClassificationSeverity.WARNING for f in self.findings)

    @property
    def error_messages(self) -> list[str]:
        """Human-readable error messages."""
        return [f.message for f in self.findings if f.severity == ClassificationSeverity.ERROR]

    @property
    def warning_messages(self) -> list[str]:
        """Human-readable warning messages."""
        return [f.message for f in self.findings if f.severity == ClassificationSeverity.WARNING]


# ---------------------------------------------------------------------------
# Pre-flight classification (runs on raw PDF bytes, before Docling)
# ---------------------------------------------------------------------------


def classify_pdf(file_content: bytes) -> PdfClassification:
    """Classify a PDF from raw bytes using pypdfium2.

    Runs lightweight structural checks (~5ms) to detect forms, encryption,
    oversized documents, and metadata-based type hints.

    Args:
        file_content: Raw PDF bytes.

    Returns:
        PdfClassification with findings and metadata.
    """
    start = time.monotonic()
    classification = PdfClassification()

    try:
        import pypdfium2  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pypdfium2 not available; skipping pre-flight classification")
        classification.elapsed_ms = int((time.monotonic() - start) * 1000)
        return classification

    # Open PDF — failure here means encrypted or corrupt
    try:
        pdf = pypdfium2.PdfDocument(BytesIO(file_content))
    except Exception as e:
        error_str = str(e).lower()
        if "password" in error_str or "encrypt" in error_str:
            classification.metadata.is_encrypted = True
            classification.findings.append(ClassificationFinding(
                code=FINDING_ENCRYPTED,
                severity=ClassificationSeverity.ERROR,
                message="PDF is password-protected. Please remove the password and resubmit.",
            ))
        else:
            classification.findings.append(ClassificationFinding(
                code=FINDING_ENCRYPTED,
                severity=ClassificationSeverity.ERROR,
                message=f"PDF could not be opened: {e}",
            ))
        classification.elapsed_ms = int((time.monotonic() - start) * 1000)
        return classification

    try:
        _extract_metadata(pdf, classification)
        _check_hard_blockers(classification)
        _check_soft_warnings(classification)
        _infer_document_type(classification)
    finally:
        pdf.close()

    classification.elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        f"PDF classified as {classification.document_type.value} "
        f"({len(classification.findings)} findings, {classification.elapsed_ms}ms)"
    )
    return classification


def _extract_metadata(pdf: object, classification: PdfClassification) -> None:
    """Extract structural metadata from an open pypdfium2 PdfDocument."""
    meta = classification.metadata

    meta.page_count = len(pdf)  # type: ignore[arg-type]

    # PDF version
    try:
        version = pdf.get_version()  # type: ignore[union-attr]
        meta.pdf_version = f"{version // 10}.{version % 10}" if isinstance(version, int) else str(version)
    except Exception:
        pass

    # Producer, creator, title
    try:
        for key, attr in [("Producer", "producer"), ("Creator", "creator"), ("Title", "title")]:
            try:
                val = pdf.get_metadata_value(key)  # type: ignore[union-attr]
                if val:
                    setattr(meta, attr, val)
            except Exception:
                pass
    except Exception:
        pass

    # Tagged PDF
    try:
        meta.is_tagged = pdf.is_tagged()  # type: ignore[union-attr]
    except Exception:
        pass

    # Form type: 0=none, 1=AcroForm, 2=XFA full, 3=XFA foreground
    try:
        meta.form_type = pdf.get_formtype()  # type: ignore[union-attr]
    except Exception:
        pass

    # Page dimensions + form field widget count
    # Widget annotations (subtype 20) are actual interactive form fields.
    # Many PDFs declare AcroForm in metadata without having real fields,
    # so we count widgets to distinguish real forms from dormant structures.
    try:
        import pypdfium2.raw as pdfium_c  # type: ignore[import-untyped]
    except ImportError:
        pdfium_c = None  # type: ignore[assignment]

    try:
        form_field_total = 0
        for i in range(meta.page_count):
            page = pdf.get_page(i)  # type: ignore[union-attr]
            try:
                w, h = page.get_size()
                meta.page_dimensions.append((round(w, 1), round(h, 1)))

                # Count widget annotations (form fields)
                if pdfium_c is not None:
                    annot_count = pdfium_c.FPDFPage_GetAnnotCount(page.raw)
                    for j in range(annot_count):
                        annot = pdfium_c.FPDFPage_GetAnnot(page.raw, j)
                        if annot:
                            # FPDF_ANNOT_WIDGET = 20
                            if pdfium_c.FPDFAnnot_GetSubtype(annot) == 20:
                                form_field_total += 1
                            pdfium_c.FPDFPage_CloseAnnot(annot)
            finally:
                page.close()
        meta.form_field_count = form_field_total
    except Exception:
        pass


def _check_hard_blockers(classification: PdfClassification) -> None:
    """Check for conditions that prevent processing entirely."""
    meta = classification.metadata

    # Forms — only block if the PDF has actual interactive form field widgets.
    # Many PDFs declare AcroForm in metadata (form_type >= 1) without having
    # any real fillable fields.  We require form_field_count > 0 to confirm
    # the PDF is genuinely a form.
    if meta.form_field_count > 0:
        if meta.form_type in (2, 3):
            # XFA full (2) or XFA foreground/hybrid (3)
            classification.findings.append(ClassificationFinding(
                code=FINDING_FORM_XFA,
                severity=ClassificationSeverity.ERROR,
                message=(
                    f"PDF contains {meta.form_field_count} interactive XFA form "
                    f"field(s). XFA forms are not supported. "
                    "Please export as a standard PDF."
                ),
                details={"form_type": meta.form_type, "form_field_count": meta.form_field_count},
            ))
        else:
            # AcroForm (1) or unknown form type with real widgets
            classification.findings.append(ClassificationFinding(
                code=FINDING_FORM_ACROFORM,
                severity=ClassificationSeverity.ERROR,
                message=(
                    f"PDF contains {meta.form_field_count} interactive form "
                    f"field(s) (AcroForm). Form content cannot be reliably "
                    "extracted. Please flatten the form or export as a standard PDF."
                ),
                details={"form_type": meta.form_type, "form_field_count": meta.form_field_count},
            ))

    # Empty
    if meta.page_count == 0:
        classification.findings.append(ClassificationFinding(
            code=FINDING_EMPTY,
            severity=ClassificationSeverity.ERROR,
            message="PDF has no pages.",
        ))

    # Too many pages
    max_pages = settings.pdf_max_pages
    if meta.page_count > max_pages:
        classification.findings.append(ClassificationFinding(
            code=FINDING_TOO_MANY_PAGES,
            severity=ClassificationSeverity.ERROR,
            message=(
                f"PDF has {meta.page_count} pages, which exceeds the "
                f"maximum of {max_pages}. Please split into smaller documents."
            ),
            details={"page_count": meta.page_count, "max_pages": max_pages},
        ))


def _check_soft_warnings(classification: PdfClassification) -> None:
    """Check for conditions that warrant a warning but allow processing."""
    meta = classification.metadata

    # Scanner-produced PDFs
    producer_lower = (meta.producer or "").lower()
    creator_lower = (meta.creator or "").lower()
    combined = f"{producer_lower} {creator_lower}"

    if any(scanner in combined for scanner in _SCANNER_PRODUCERS):
        classification.findings.append(ClassificationFinding(
            code=FINDING_SCAN_PRODUCER,
            severity=ClassificationSeverity.WARNING,
            message=(
                "PDF appears to have been created by scanning software. "
                "Text extraction may be incomplete if the document is image-only."
            ),
            details={"producer": meta.producer, "creator": meta.creator},
        ))

    # Oversized pages (> A0 size: 2384 x 3370 points)
    a0_area = 2384 * 3370
    oversized_pages = []
    for i, (w, h) in enumerate(meta.page_dimensions):
        if w * h > a0_area * 1.1:  # 10% tolerance
            oversized_pages.append(i + 1)
    if oversized_pages:
        classification.findings.append(ClassificationFinding(
            code=FINDING_OVERSIZED_PAGES,
            severity=ClassificationSeverity.WARNING,
            message=(
                f"Document contains {len(oversized_pages)} oversized page(s) "
                f"(larger than A0). Layout extraction may be degraded."
            ),
            details={"oversized_pages": oversized_pages},
        ))

    # Mixed orientation
    if len(meta.page_dimensions) >= 2:
        orientations = set()
        for w, h in meta.page_dimensions:
            if w > 0 and h > 0:
                orientations.add("landscape" if w > h * 1.05 else "portrait" if h > w * 1.05 else "square")
        if len(orientations) > 1:
            classification.findings.append(ClassificationFinding(
                code=FINDING_MIXED_ORIENTATION,
                severity=ClassificationSeverity.WARNING,
                message=(
                    "Document contains mixed page orientations (portrait and landscape). "
                    "Landscape pages may have layout issues in the converted output."
                ),
                details={"orientations": sorted(orientations)},
            ))

    # Presentation producer
    if any(p in combined for p in _PRESENTATION_PRODUCERS):
        classification.findings.append(ClassificationFinding(
            code=FINDING_PRESENTATION,
            severity=ClassificationSeverity.WARNING,
            message=(
                "Document appears to be exported from presentation software. "
                "Slide layouts may not convert cleanly to linear markdown."
            ),
            details={"producer": meta.producer, "creator": meta.creator},
        ))

    # Spreadsheet producer
    if any(s in combined for s in _SPREADSHEET_PRODUCERS):
        classification.findings.append(ClassificationFinding(
            code=FINDING_SPREADSHEET,
            severity=ClassificationSeverity.WARNING,
            message=(
                "Document appears to be an exported spreadsheet. "
                "Complex table structures may lose formatting."
            ),
            details={"producer": meta.producer, "creator": meta.creator},
        ))


def _infer_document_type(classification: PdfClassification) -> None:
    """Set the document_type based on findings."""
    codes = {f.code for f in classification.findings}

    if FINDING_FORM_ACROFORM in codes or FINDING_FORM_XFA in codes:
        classification.document_type = PdfDocumentType.FORM
    elif FINDING_SCANNED in codes or FINDING_SCAN_PRODUCER in codes:
        classification.document_type = PdfDocumentType.SCANNED
    elif FINDING_PRESENTATION in codes:
        classification.document_type = PdfDocumentType.PRESENTATION
    elif FINDING_SPREADSHEET in codes:
        classification.document_type = PdfDocumentType.SPREADSHEET
    elif not classification.has_errors:
        classification.document_type = PdfDocumentType.STANDARD


# ---------------------------------------------------------------------------
# Post-extraction enrichment (runs after Docling, before LLM phases)
# ---------------------------------------------------------------------------


def enrich_classification(
    classification: PdfClassification,
    *,
    total_pages: int,
    total_chars: int,
    figure_count: int,
    layout_hints: dict[str, str],
) -> None:
    """Enrich classification with content-based signals from Docling extraction.

    Called after _step_docling() with extraction stats. Adds or refines
    findings based on actual document content.

    Args:
        classification: Pre-flight classification to enrich.
        total_pages: Number of pages extracted.
        total_chars: Total characters across all pages.
        figure_count: Number of figures extracted.
        layout_hints: Per-page layout hints from column detection.
    """
    if total_pages == 0:
        return

    chars_per_page = total_chars / total_pages

    # Scanned document detection (refine pre-flight signal)
    existing_codes = {f.code for f in classification.findings}
    if chars_per_page < 50 and FINDING_SCANNED not in existing_codes:
        classification.findings.append(ClassificationFinding(
            code=FINDING_SCANNED,
            severity=ClassificationSeverity.WARNING,
            message=(
                "Document appears to be scanned or image-only "
                f"(average {chars_per_page:.0f} characters per page). "
                "Text extraction may be incomplete."
            ),
            details={"chars_per_page": round(chars_per_page, 1)},
        ))

    # Low text density (not quite scanned, but sparse)
    if 50 <= chars_per_page < 200 and FINDING_LOW_TEXT_DENSITY not in existing_codes:
        classification.findings.append(ClassificationFinding(
            code=FINDING_LOW_TEXT_DENSITY,
            severity=ClassificationSeverity.WARNING,
            message=(
                f"Document has low text density ({chars_per_page:.0f} characters per page). "
                "Output quality may be limited."
            ),
            details={"chars_per_page": round(chars_per_page, 1)},
        ))

    # Presentation detection from layout hints (all/most pages are presentation-like)
    if FINDING_PRESENTATION not in existing_codes and total_pages >= 3:
        presentation_count = sum(1 for h in layout_hints.values() if h == "presentation")
        if presentation_count / total_pages >= 0.6:
            classification.findings.append(ClassificationFinding(
                code=FINDING_PRESENTATION,
                severity=ClassificationSeverity.WARNING,
                message=(
                    "Document layout suggests presentation slides. "
                    "Slide layouts may not convert cleanly to linear markdown."
                ),
                details={"presentation_pages": presentation_count, "total_pages": total_pages},
            ))

    # Re-infer document type with enriched findings
    _infer_document_type(classification)
