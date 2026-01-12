"""Unit tests for VisionExtractionService."""

from src.services.vision_extraction_service import is_scanned_pdf


class TestIsScannedPdf:
    """Tests for scanned PDF detection."""

    def test_image_placeholders_detected_as_scanned(self) -> None:
        """Documents with only image placeholders should be detected as scanned."""
        markdown = "<!-- image -->\n\n<!-- image -->\n\n<!-- image -->"
        assert is_scanned_pdf(markdown, total_pages=1) is True

    def test_many_image_placeholders_still_scanned(self) -> None:
        """Even many placeholders shouldn't fool the detector.

        Simulates the Wellness Center document case where Docling output
        8 image placeholders on page 1 and 2 on page 2, totaling 156 chars
        which would exceed the 50 chars/page threshold if not stripped.
        """
        # 10 placeholders across 2 pages = 156 chars raw, but 0 text chars
        markdown = ("<!-- image -->\n\n" * 8) + ("<!-- image -->\n\n" * 2)
        assert is_scanned_pdf(markdown, total_pages=2) is True

    def test_actual_text_not_flagged_as_scanned(self) -> None:
        """Documents with real text content should not be flagged."""
        markdown = (
            "# Document Title\n\n"
            "This is actual paragraph content with many words that should "
            "exceed the threshold for scanned detection.\n\n"
            "<!-- image -->"
        )
        assert is_scanned_pdf(markdown, total_pages=1) is False

    def test_mixed_content_with_sufficient_text(self) -> None:
        """Documents with placeholders but sufficient real text pass."""
        markdown = (
            "<!-- image -->\n\n"
            "# Introduction\n\n"
            "This document contains real textual content mixed with images. "
            "The text density should be high enough to not trigger scanned detection.\n\n"
            "<!-- image -->\n\n"
            "## Section Two\n\n"
            "More content here with actual words and sentences."
        )
        assert is_scanned_pdf(markdown, total_pages=1) is False

    def test_empty_document_is_scanned(self) -> None:
        """Empty documents should be detected as scanned."""
        assert is_scanned_pdf("", total_pages=1) is True

    def test_whitespace_only_is_scanned(self) -> None:
        """Whitespace-only documents should be detected as scanned."""
        assert is_scanned_pdf("   \n\n   ", total_pages=1) is True

    def test_zero_pages_is_scanned(self) -> None:
        """Zero pages should return True (edge case)."""
        assert is_scanned_pdf("any content", total_pages=0) is True

    def test_markdown_images_stripped(self) -> None:
        """Markdown image syntax should also be stripped."""
        markdown = "![alt text](image.png)\n\n![another](pic.jpg)"
        assert is_scanned_pdf(markdown, total_pages=1) is True

    def test_markdown_images_with_text(self) -> None:
        """Markdown images with surrounding text should pass if enough text."""
        markdown = (
            "# Document Title\n\n"
            "This is a paragraph with lots of words before an image.\n\n"
            "![Figure 1](figure1.png)\n\n"
            "And this is another paragraph with content after the image."
        )
        assert is_scanned_pdf(markdown, total_pages=1) is False

    def test_multiple_pages_calculation(self) -> None:
        """Chars per page should be calculated correctly for multi-page docs."""
        # 100 chars spread across 10 pages = 10 chars/page = scanned
        markdown = "a" * 100
        assert is_scanned_pdf(markdown, total_pages=10) is True

        # 100 chars spread across 1 page = 100 chars/page = not scanned
        assert is_scanned_pdf(markdown, total_pages=1) is False

    def test_html_comments_with_content_stripped(self) -> None:
        """HTML comments with various content should be stripped."""
        markdown = (
            "<!-- This is a longer comment with more text -->\n"
            "<!-- Another comment -->\n"
            "<!-- image -->\n"
        )
        assert is_scanned_pdf(markdown, total_pages=1) is True

    def test_threshold_boundary(self) -> None:
        """Test behavior at the 50 chars/page threshold boundary."""
        # Exactly at threshold (50 chars, 1 page) - should NOT be scanned
        markdown = "a" * 50
        assert is_scanned_pdf(markdown, total_pages=1) is False

        # Just below threshold (49 chars, 1 page) - should be scanned
        markdown = "a" * 49
        assert is_scanned_pdf(markdown, total_pages=1) is True
