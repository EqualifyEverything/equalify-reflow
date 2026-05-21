"""Unit tests for PDF classification service.

Tests pre-flight classification (pypdfium2-based metadata checks) and
post-extraction enrichment (content-based signal refinement).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.services.pdf_classifier import (
    FINDING_EMPTY,
    FINDING_ENCRYPTED,
    FINDING_FORM_ACROFORM,
    FINDING_FORM_XFA,
    FINDING_LOW_TEXT_DENSITY,
    FINDING_MIXED_ORIENTATION,
    FINDING_OVERSIZED_PAGES,
    FINDING_PRESENTATION,
    FINDING_SCAN_PRODUCER,
    FINDING_SCANNED,
    FINDING_SPREADSHEET,
    FINDING_TOO_MANY_PAGES,
    ClassificationFinding,
    ClassificationSeverity,
    PdfClassification,
    PdfDocumentType,
    PdfMetadata,
    _check_hard_blockers,
    _check_soft_warnings,
    _infer_document_type,
    classify_pdf,
    enrich_classification,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pdf(
    *,
    page_count: int = 5,
    form_type: int = 0,
    version: int = 17,
    producer: str = "Microsoft Word",
    creator: str = "Microsoft Word",
    title: str = "Test Document",
    is_tagged: bool = False,
    page_size: tuple[float, float] = (612.0, 792.0),  # US Letter
    form_fields_per_page: int = 0,
    open_error: Exception | None = None,
) -> MagicMock:
    """Create a mock pypdfium2.PdfDocument."""
    mock_pdf = MagicMock()

    if open_error:
        mock_pdf.side_effect = open_error

    mock_pdf.__len__ = MagicMock(return_value=page_count)
    mock_pdf.get_version.return_value = version
    mock_pdf.get_formtype.return_value = form_type
    mock_pdf.is_tagged.return_value = is_tagged

    def get_metadata_value(key: str) -> str:
        return {"Producer": producer, "Creator": creator, "Title": title}.get(key, "")

    mock_pdf.get_metadata_value = MagicMock(side_effect=get_metadata_value)

    # Pages with dimensions and raw handles for annotation counting
    def make_page(idx: int) -> MagicMock:
        page = MagicMock()
        page.get_size.return_value = page_size
        # raw handle used by pdfium_c.FPDFPage_GetAnnotCount
        page.raw = MagicMock()
        return page

    mock_pdf.get_page = MagicMock(side_effect=make_page)
    mock_pdf._form_fields_per_page = form_fields_per_page

    return mock_pdf


def _classification_codes(classification: PdfClassification) -> set[str]:
    """Extract finding codes from a classification."""
    return {f.code for f in classification.findings}


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestPdfClassificationModel:
    """Tests for PdfClassification properties."""

    def test_has_errors_with_error_finding(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code="test", severity=ClassificationSeverity.ERROR, message="fail"
                )
            ]
        )
        assert c.has_errors is True
        assert c.has_warnings is False

    def test_has_warnings_with_warning_finding(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code="test", severity=ClassificationSeverity.WARNING, message="warn"
                )
            ]
        )
        assert c.has_errors is False
        assert c.has_warnings is True

    def test_empty_findings(self):
        c = PdfClassification()
        assert c.has_errors is False
        assert c.has_warnings is False
        assert c.error_messages == []
        assert c.warning_messages == []

    def test_error_messages(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code="a", severity=ClassificationSeverity.ERROR, message="error one"
                ),
                ClassificationFinding(
                    code="b", severity=ClassificationSeverity.WARNING, message="warn one"
                ),
                ClassificationFinding(
                    code="c", severity=ClassificationSeverity.ERROR, message="error two"
                ),
            ]
        )
        assert c.error_messages == ["error one", "error two"]
        assert c.warning_messages == ["warn one"]


# ---------------------------------------------------------------------------
# Hard blocker tests
# ---------------------------------------------------------------------------


class TestHardBlockers:
    """Tests for _check_hard_blockers."""

    def test_acroform_with_fields_detected(self):
        """AcroForm with real widget fields → warn, not block (reconstructed visually)."""
        c = PdfClassification(metadata=PdfMetadata(page_count=5, form_type=1, form_field_count=10))
        _check_hard_blockers(c)
        assert FINDING_FORM_ACROFORM in _classification_codes(c)
        assert c.findings[0].severity == ClassificationSeverity.WARNING
        assert not c.has_errors
        assert "10" in c.findings[0].message

    def test_acroform_without_fields_is_clean(self):
        """AcroForm declared but zero widget fields → not a real form, allow."""
        c = PdfClassification(metadata=PdfMetadata(page_count=5, form_type=1, form_field_count=0))
        _check_hard_blockers(c)
        assert FINDING_FORM_ACROFORM not in _classification_codes(c)

    def test_xfa_full_with_fields_detected(self):
        """XFA full (form_type=2) with fields → block as XFA."""
        c = PdfClassification(metadata=PdfMetadata(page_count=5, form_type=2, form_field_count=50))
        _check_hard_blockers(c)
        assert FINDING_FORM_XFA in _classification_codes(c)
        assert c.findings[0].severity == ClassificationSeverity.ERROR

    def test_xfa_foreground_with_fields_detected(self):
        """XFA foreground/hybrid (form_type=3) with fields → block as XFA."""
        c = PdfClassification(metadata=PdfMetadata(page_count=5, form_type=3, form_field_count=284))
        _check_hard_blockers(c)
        assert FINDING_FORM_XFA in _classification_codes(c)
        assert "284" in c.findings[0].message

    def test_no_form_is_clean(self):
        c = PdfClassification(metadata=PdfMetadata(page_count=5, form_type=0))
        _check_hard_blockers(c)
        assert len(c.findings) == 0

    def test_empty_pdf(self):
        c = PdfClassification(metadata=PdfMetadata(page_count=0))
        _check_hard_blockers(c)
        assert FINDING_EMPTY in _classification_codes(c)

    @patch("src.services.pdf_classifier.settings")
    def test_too_many_pages(self, mock_settings):
        mock_settings.pdf_max_pages = 50
        c = PdfClassification(metadata=PdfMetadata(page_count=51))
        _check_hard_blockers(c)
        assert FINDING_TOO_MANY_PAGES in _classification_codes(c)
        assert "51" in c.findings[0].message
        assert "50" in c.findings[0].message

    @patch("src.services.pdf_classifier.settings")
    def test_exactly_max_pages_is_ok(self, mock_settings):
        mock_settings.pdf_max_pages = 50
        c = PdfClassification(metadata=PdfMetadata(page_count=50))
        _check_hard_blockers(c)
        assert FINDING_TOO_MANY_PAGES not in _classification_codes(c)


# ---------------------------------------------------------------------------
# Soft warning tests
# ---------------------------------------------------------------------------


class TestSoftWarnings:
    """Tests for _check_soft_warnings."""

    def test_scanner_producer_detected(self):
        c = PdfClassification(
            metadata=PdfMetadata(page_count=5, producer="Adobe Scan 21.09")
        )
        _check_soft_warnings(c)
        assert FINDING_SCAN_PRODUCER in _classification_codes(c)
        assert c.findings[0].severity == ClassificationSeverity.WARNING

    def test_scanner_creator_detected(self):
        c = PdfClassification(
            metadata=PdfMetadata(page_count=5, creator="CamScanner Pro")
        )
        _check_soft_warnings(c)
        assert FINDING_SCAN_PRODUCER in _classification_codes(c)

    def test_normal_producer_no_warning(self):
        c = PdfClassification(
            metadata=PdfMetadata(page_count=5, producer="Microsoft Word 2021")
        )
        _check_soft_warnings(c)
        assert FINDING_SCAN_PRODUCER not in _classification_codes(c)

    def test_oversized_pages(self):
        # A0 is 2384 x 3370; make page much bigger
        c = PdfClassification(
            metadata=PdfMetadata(
                page_count=2,
                page_dimensions=[(612.0, 792.0), (5000.0, 5000.0)],
            )
        )
        _check_soft_warnings(c)
        assert FINDING_OVERSIZED_PAGES in _classification_codes(c)
        assert c.findings[0].details["oversized_pages"] == [2]

    def test_normal_pages_no_oversized_warning(self):
        c = PdfClassification(
            metadata=PdfMetadata(
                page_count=2,
                page_dimensions=[(612.0, 792.0), (612.0, 792.0)],
            )
        )
        _check_soft_warnings(c)
        assert FINDING_OVERSIZED_PAGES not in _classification_codes(c)

    def test_mixed_orientation(self):
        c = PdfClassification(
            metadata=PdfMetadata(
                page_count=3,
                page_dimensions=[
                    (612.0, 792.0),   # portrait
                    (792.0, 612.0),   # landscape
                    (612.0, 792.0),   # portrait
                ],
            )
        )
        _check_soft_warnings(c)
        assert FINDING_MIXED_ORIENTATION in _classification_codes(c)

    def test_uniform_orientation_no_warning(self):
        c = PdfClassification(
            metadata=PdfMetadata(
                page_count=2,
                page_dimensions=[(612.0, 792.0), (612.0, 792.0)],
            )
        )
        _check_soft_warnings(c)
        assert FINDING_MIXED_ORIENTATION not in _classification_codes(c)

    def test_single_page_no_mixed_orientation(self):
        c = PdfClassification(
            metadata=PdfMetadata(
                page_count=1,
                page_dimensions=[(612.0, 792.0)],
            )
        )
        _check_soft_warnings(c)
        assert FINDING_MIXED_ORIENTATION not in _classification_codes(c)

    def test_presentation_producer(self):
        c = PdfClassification(
            metadata=PdfMetadata(page_count=10, producer="Microsoft PowerPoint 2019")
        )
        _check_soft_warnings(c)
        assert FINDING_PRESENTATION in _classification_codes(c)

    def test_keynote_producer(self):
        c = PdfClassification(
            metadata=PdfMetadata(page_count=10, creator="Keynote 12.0")
        )
        _check_soft_warnings(c)
        assert FINDING_PRESENTATION in _classification_codes(c)

    def test_spreadsheet_producer(self):
        c = PdfClassification(
            metadata=PdfMetadata(page_count=3, producer="Microsoft Excel 2021")
        )
        _check_soft_warnings(c)
        assert FINDING_SPREADSHEET in _classification_codes(c)


# ---------------------------------------------------------------------------
# Document type inference tests
# ---------------------------------------------------------------------------


class TestInferDocumentType:
    """Tests for _infer_document_type."""

    def test_form_type(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_FORM_ACROFORM,
                    severity=ClassificationSeverity.ERROR,
                    message="form",
                )
            ]
        )
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.FORM

    def test_scanned_type(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_SCANNED,
                    severity=ClassificationSeverity.WARNING,
                    message="scanned",
                )
            ]
        )
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.SCANNED

    def test_scan_producer_type(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_SCAN_PRODUCER,
                    severity=ClassificationSeverity.WARNING,
                    message="scan producer",
                )
            ]
        )
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.SCANNED

    def test_presentation_type(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_PRESENTATION,
                    severity=ClassificationSeverity.WARNING,
                    message="slides",
                )
            ]
        )
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.PRESENTATION

    def test_spreadsheet_type(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_SPREADSHEET,
                    severity=ClassificationSeverity.WARNING,
                    message="sheet",
                )
            ]
        )
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.SPREADSHEET

    def test_standard_no_findings(self):
        c = PdfClassification()
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.STANDARD

    def test_form_takes_precedence_over_scanned(self):
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_FORM_ACROFORM,
                    severity=ClassificationSeverity.ERROR,
                    message="form",
                ),
                ClassificationFinding(
                    code=FINDING_SCANNED,
                    severity=ClassificationSeverity.WARNING,
                    message="scanned",
                ),
            ]
        )
        _infer_document_type(c)
        assert c.document_type == PdfDocumentType.FORM


# ---------------------------------------------------------------------------
# classify_pdf integration tests (with mocked pypdfium2)
# ---------------------------------------------------------------------------


class TestClassifyPdf:
    """Tests for classify_pdf with mocked pypdfium2.PdfDocument.

    Note: The low-level pdfium_c annotation counting cannot be mocked
    through mock PdfDocument pages (they don't have real PDFium handles).
    Form detection tests that need specific form_field_count values are
    covered by TestHardBlockers above.  These integration tests verify
    the full classify_pdf flow for non-form scenarios and use
    _extract_metadata + _check_hard_blockers patching for form cases.
    """

    @patch("pypdfium2.PdfDocument")
    def test_standard_pdf(self, mock_pdf_cls):
        mock_pdf_cls.return_value = _make_mock_pdf()

        result = classify_pdf(b"fake-pdf-bytes")

        assert result.document_type == PdfDocumentType.STANDARD
        assert not result.has_errors
        assert result.metadata.page_count == 5
        assert result.elapsed_ms >= 0

    @patch("pypdfium2.PdfDocument")
    def test_encrypted_pdf(self, mock_pdf_cls):
        mock_pdf_cls.side_effect = Exception("password required")

        result = classify_pdf(b"fake-pdf-bytes")

        assert result.has_errors
        assert FINDING_ENCRYPTED in _classification_codes(result)
        assert result.metadata.is_encrypted

    @patch("src.services.pdf_classifier._extract_metadata")
    @patch("pypdfium2.PdfDocument")
    def test_form_pdf_with_fields_warns_not_blocked(self, mock_pdf_cls, mock_extract):
        """AcroForm with actual widget fields → warn (reconstructed visually), not blocked."""
        mock_pdf_cls.return_value = _make_mock_pdf(form_type=1)

        def populate_metadata(pdf, classification):
            classification.metadata.page_count = 5
            classification.metadata.form_type = 1
            classification.metadata.form_field_count = 50

        mock_extract.side_effect = populate_metadata

        result = classify_pdf(b"fake-pdf-bytes")

        assert not result.has_errors
        assert result.has_warnings
        assert result.document_type == PdfDocumentType.FORM
        assert FINDING_FORM_ACROFORM in _classification_codes(result)

    @patch("pypdfium2.PdfDocument")
    def test_form_type_without_fields_allowed(self, mock_pdf_cls):
        """AcroForm declared but no real widget fields → not blocked."""
        mock_pdf_cls.return_value = _make_mock_pdf(form_type=1)

        result = classify_pdf(b"fake-pdf-bytes")

        # form_field_count stays 0 because mock pages have no real annotations
        assert not result.has_errors
        assert FINDING_FORM_ACROFORM not in _classification_codes(result)

    @patch("src.services.pdf_classifier._extract_metadata")
    @patch("pypdfium2.PdfDocument")
    def test_xfa_form_blocked(self, mock_pdf_cls, mock_extract):
        mock_pdf_cls.return_value = _make_mock_pdf(form_type=2)

        def populate_metadata(pdf, classification):
            classification.metadata.page_count = 5
            classification.metadata.form_type = 2
            classification.metadata.form_field_count = 50

        mock_extract.side_effect = populate_metadata

        result = classify_pdf(b"fake-pdf-bytes")

        assert result.has_errors
        assert FINDING_FORM_XFA in _classification_codes(result)

    @patch("src.services.pdf_classifier._extract_metadata")
    @patch("pypdfium2.PdfDocument")
    def test_xfa_foreground_blocked(self, mock_pdf_cls, mock_extract):
        """form_type=3 (XFA foreground/hybrid) with fields → blocked as XFA."""
        mock_pdf_cls.return_value = _make_mock_pdf(form_type=3)

        def populate_metadata(pdf, classification):
            classification.metadata.page_count = 11
            classification.metadata.form_type = 3
            classification.metadata.form_field_count = 284

        mock_extract.side_effect = populate_metadata

        result = classify_pdf(b"fake-pdf-bytes")

        assert result.has_errors
        assert FINDING_FORM_XFA in _classification_codes(result)

    @patch("pypdfium2.PdfDocument")
    def test_too_many_pages_blocked(self, mock_pdf_cls):
        mock_pdf_cls.return_value = _make_mock_pdf(page_count=60)

        result = classify_pdf(b"fake-pdf-bytes")

        assert result.has_errors
        assert FINDING_TOO_MANY_PAGES in _classification_codes(result)

    @patch("pypdfium2.PdfDocument")
    def test_scanner_producer_warns(self, mock_pdf_cls):
        mock_pdf_cls.return_value = _make_mock_pdf(producer="Adobe Scan 21.09.24")

        result = classify_pdf(b"fake-pdf-bytes")

        assert not result.has_errors
        assert result.has_warnings
        assert FINDING_SCAN_PRODUCER in _classification_codes(result)
        assert result.document_type == PdfDocumentType.SCANNED

    @patch("pypdfium2.PdfDocument")
    def test_metadata_extraction(self, mock_pdf_cls):
        mock_pdf_cls.return_value = _make_mock_pdf(
            page_count=10,
            version=20,
            producer="LaTeX",
            creator="pdfTeX",
            title="My Thesis",
            is_tagged=True,
        )

        result = classify_pdf(b"fake-pdf-bytes")

        assert result.metadata.page_count == 10
        assert result.metadata.pdf_version == "2.0"
        assert result.metadata.producer == "LaTeX"
        assert result.metadata.creator == "pdfTeX"
        assert result.metadata.title == "My Thesis"
        assert result.metadata.is_tagged is True


# ---------------------------------------------------------------------------
# Post-extraction enrichment tests
# ---------------------------------------------------------------------------


class TestEnrichClassification:
    """Tests for enrich_classification."""

    def test_scanned_document_detected(self):
        c = PdfClassification()
        enrich_classification(
            c, total_pages=10, total_chars=200, figure_count=0, layout_hints={}
        )
        assert FINDING_SCANNED in _classification_codes(c)
        assert c.document_type == PdfDocumentType.SCANNED

    def test_low_text_density_detected(self):
        c = PdfClassification()
        enrich_classification(
            c, total_pages=10, total_chars=1500, figure_count=0, layout_hints={}
        )
        assert FINDING_LOW_TEXT_DENSITY in _classification_codes(c)

    def test_normal_density_no_warning(self):
        c = PdfClassification()
        enrich_classification(
            c, total_pages=10, total_chars=5000, figure_count=0, layout_hints={}
        )
        assert FINDING_SCANNED not in _classification_codes(c)
        assert FINDING_LOW_TEXT_DENSITY not in _classification_codes(c)

    def test_zero_pages_skipped(self):
        c = PdfClassification()
        enrich_classification(
            c, total_pages=0, total_chars=0, figure_count=0, layout_hints={}
        )
        assert len(c.findings) == 0

    def test_presentation_from_layout_hints(self):
        c = PdfClassification()
        layout_hints = {str(i): "presentation" for i in range(1, 11)}
        enrich_classification(
            c, total_pages=10, total_chars=5000, figure_count=5, layout_hints=layout_hints
        )
        assert FINDING_PRESENTATION in _classification_codes(c)
        assert c.document_type == PdfDocumentType.PRESENTATION

    def test_presentation_threshold_not_met(self):
        c = PdfClassification()
        layout_hints = {
            "1": "presentation",
            "2": "single_column",
            "3": "single_column",
            "4": "single_column",
            "5": "single_column",
        }
        enrich_classification(
            c, total_pages=5, total_chars=5000, figure_count=0, layout_hints=layout_hints
        )
        assert FINDING_PRESENTATION not in _classification_codes(c)

    def test_no_duplicate_scanned_finding(self):
        """If pre-flight already found scanned, don't add again."""
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_SCANNED,
                    severity=ClassificationSeverity.WARNING,
                    message="already found",
                )
            ]
        )
        enrich_classification(
            c, total_pages=10, total_chars=200, figure_count=0, layout_hints={}
        )
        scanned_findings = [f for f in c.findings if f.code == FINDING_SCANNED]
        assert len(scanned_findings) == 1

    def test_no_duplicate_presentation_finding(self):
        """If pre-flight found presentation producer, don't add layout-based one."""
        c = PdfClassification(
            findings=[
                ClassificationFinding(
                    code=FINDING_PRESENTATION,
                    severity=ClassificationSeverity.WARNING,
                    message="already found",
                )
            ]
        )
        layout_hints = {str(i): "presentation" for i in range(1, 11)}
        enrich_classification(
            c, total_pages=10, total_chars=5000, figure_count=5, layout_hints=layout_hints
        )
        pres_findings = [f for f in c.findings if f.code == FINDING_PRESENTATION]
        assert len(pres_findings) == 1

    def test_enrichment_updates_document_type(self):
        """After enrichment, document type is re-inferred."""
        c = PdfClassification(document_type=PdfDocumentType.STANDARD)
        enrich_classification(
            c, total_pages=10, total_chars=100, figure_count=0, layout_hints={}
        )
        assert c.document_type == PdfDocumentType.SCANNED
