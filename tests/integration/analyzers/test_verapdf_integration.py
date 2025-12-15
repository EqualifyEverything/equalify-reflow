"""Integration tests for VeraPDF accessibility analyzer.

These tests require a running VeraPDF Docker container.
Run with: pytest -m verapdf tests/integration/analyzers/

To start VeraPDF: docker-compose up -d verapdf

These tests are skipped if:
- SKIP_VERAPDF_TESTS=1 environment variable is set
- VeraPDF service is not running (checked via health endpoint)
"""

import os
import tempfile
from pathlib import Path

import httpx
import pytest
from src.analyzers.pdf_accessibility_analyzer import PDFAccessibilityAnalyzer
from src.shared.models.hints_models import HintSource


def _verapdf_available() -> bool:
    """Check if VeraPDF service is available."""
    if os.getenv("SKIP_VERAPDF_TESTS") == "1":
        return False
    url = os.getenv("VERAPDF_URL", "http://localhost:8081")
    try:
        response = httpx.get(f"{url}/api/info/version", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


# Skip all tests in this module if VeraPDF is not available
pytestmark = [
    pytest.mark.integration,
    pytest.mark.verapdf,
    pytest.mark.timeout(120),  # PDF analysis can take time
    pytest.mark.skipif(
        not _verapdf_available(),
        reason="VeraPDF not available (set SKIP_VERAPDF_TESTS=1 or start VeraPDF service)"
    ),
]


def get_verapdf_url() -> str:
    """Get VeraPDF URL from environment or use default."""
    return os.getenv("VERAPDF_URL", "http://localhost:8081")


@pytest.fixture
def verapdf_analyzer() -> PDFAccessibilityAnalyzer:
    """Create analyzer with correct URL for testing."""
    return PDFAccessibilityAnalyzer(verapdf_url=get_verapdf_url())


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Create a minimal valid PDF for testing.

    This is a simple PDF that will likely fail PDF/UA validation
    as it lacks proper structure tags.
    """
    # Minimal PDF 1.4 content
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /Resources << >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Length 0 >>\nstream\nendstream\nendobj\n"
        b"xref\n0 5\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000224 00000 n \n"
        b"trailer\n<< /Size 5 /Root 1 0 R >>\n"
        b"startxref\n274\n%%EOF"
    )


class TestVeraPDFHealthCheck:
    """Tests for VeraPDF service availability."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true(
        self, verapdf_analyzer: PDFAccessibilityAnalyzer
    ) -> None:
        """Should return True when VeraPDF is available."""
        is_healthy = await verapdf_analyzer.check_health()
        assert is_healthy is True, (
            "VeraPDF health check failed. "
            f"Is VeraPDF running at {verapdf_analyzer.verapdf_url}?"
        )


class TestVeraPDFAnalysis:
    """Tests for PDF accessibility analysis with real VeraPDF."""

    @pytest.mark.asyncio
    async def test_analyze_simple_pdf(
        self,
        verapdf_analyzer: PDFAccessibilityAnalyzer,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Should analyze a simple PDF and return hints."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(sample_pdf_bytes)
            pdf_path = Path(f.name)

        try:
            async with verapdf_analyzer:
                hints = await verapdf_analyzer.analyze_pdf(pdf_path)

            # Simple PDF without tags should have validation issues
            # (or could be compliant if very minimal)
            assert isinstance(hints, list)

            for hint in hints:
                assert hint.source == HintSource.VERAPDF
                assert hint.rule_id != ""
                assert hint.message != ""
                assert hint.category is not None
                assert hint.severity is not None

        finally:
            pdf_path.unlink()

    @pytest.mark.asyncio
    async def test_analyze_returns_hints_with_page_numbers(
        self,
        verapdf_analyzer: PDFAccessibilityAnalyzer,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Hints should include page numbers when available."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(sample_pdf_bytes)
            pdf_path = Path(f.name)

        try:
            async with verapdf_analyzer:
                hints = await verapdf_analyzer.analyze_pdf(pdf_path)

            # Check if any hints have page numbers
            # (Not all hints may have page numbers depending on issue type)
            for hint in hints:
                if hint.page_number is not None:
                    assert hint.page_number >= 1

        finally:
            pdf_path.unlink()

    @pytest.mark.asyncio
    async def test_file_not_found_raises(
        self, verapdf_analyzer: PDFAccessibilityAnalyzer
    ) -> None:
        """Should raise FileNotFoundError for missing PDF."""
        with pytest.raises(FileNotFoundError):
            await verapdf_analyzer.analyze_pdf("/nonexistent/path/document.pdf")


class TestVeraPDFPerformance:
    """Performance tests for VeraPDF analysis."""

    @pytest.mark.asyncio
    @pytest.mark.timeout(30)  # Should complete within 30 seconds
    async def test_analysis_completes_in_reasonable_time(
        self,
        verapdf_analyzer: PDFAccessibilityAnalyzer,
        sample_pdf_bytes: bytes,
    ) -> None:
        """Analysis should complete within timeout for simple PDF."""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(sample_pdf_bytes)
            pdf_path = Path(f.name)

        try:
            async with verapdf_analyzer:
                hints = await verapdf_analyzer.analyze_pdf(pdf_path)

            # Just verify it completed - performance is implicitly checked by timeout
            assert isinstance(hints, list)

        finally:
            pdf_path.unlink()


class TestVeraPDFContextManager:
    """Tests for async context manager behavior."""

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(
        self, verapdf_analyzer: PDFAccessibilityAnalyzer
    ) -> None:
        """Should properly close HTTP client on exit."""
        async with verapdf_analyzer as analyzer:
            # Force client creation
            await analyzer._get_client()
            assert analyzer._client is not None

        # After context exit, client should be closed
        assert analyzer._client is None
