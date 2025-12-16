"""PDF accessibility analyzer using VeraPDF.

This module wraps the VeraPDF REST API to detect PDF/UA-1 accessibility
issues that should be addressed during document conversion.
"""

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.shared.models.hints_models import (
    HintCategory,
    HintSeverity,
    HintSource,
    IssueHint,
)

logger = logging.getLogger(__name__)


# VeraPDF rule to HintCategory mapping
# Maps PDF/UA-1 rule specifications to categories for routing to specialist agents
VERAPDF_CATEGORY_MAP: dict[str, HintCategory] = {
    # Structure rules - document structure, headings, reading order
    "ISO_32000-1:2008": HintCategory.STRUCTURE,  # PDF structure
    "ISO_32000_2008": HintCategory.STRUCTURE,  # PDF structure (alternate)
    "ISO_14289-1:2014": HintCategory.STRUCTURE,  # PDF/UA-1 structure
    "ISO_14289_1_2014": HintCategory.STRUCTURE,  # PDF/UA-1 (alternate)
    # WCAG structure rules
    "WCAG2AAA-1-3-1": HintCategory.STRUCTURE,  # Info and relationships
    "WCAG2AA-1-3-1": HintCategory.STRUCTURE,
    # Figure/image rules
    "WCAG2AAA-1-1-1": HintCategory.FIGURES,  # Non-text content (alt text)
    "WCAG2AA-1-1-1": HintCategory.FIGURES,
    # Table rules
    "WCAG2AAA-1-3-1-H51": HintCategory.TABLES,  # Table markup
    "WCAG2AAA-1-3-1-H63": HintCategory.TABLES,  # Table headers
    "WCAG2AA-1-3-1-H51": HintCategory.TABLES,
    "WCAG2AA-1-3-1-H63": HintCategory.TABLES,
}

# Clause patterns for category detection (when spec doesn't match)
CLAUSE_CATEGORY_MAP: dict[str, HintCategory] = {
    "7.1": HintCategory.STRUCTURE,  # General structure
    "7.2": HintCategory.STRUCTURE,  # Document structure
    "7.3": HintCategory.STRUCTURE,  # Headings
    "7.4": HintCategory.TABLES,  # Tables
    "7.5": HintCategory.STRUCTURE,  # Lists
    "7.6": HintCategory.FIGURES,  # Artifacts (images/decorative)
    "7.7": HintCategory.FIGURES,  # Annotations
    "7.8": HintCategory.TYPOGRAPHY,  # Optional content
    "7.9": HintCategory.GENERAL,  # Metadata
    "7.10": HintCategory.FIGURES,  # Alt text
    "7.11": HintCategory.GENERAL,  # Embedded files
    "7.12": HintCategory.FIGURES,  # Multimedia
    "7.13": HintCategory.TYPOGRAPHY,  # Running headers/footers
    "7.18": HintCategory.FIGURES,  # Figures
    "7.21": HintCategory.STRUCTURE,  # Lists
}

# Keywords in description for category detection
KEYWORD_CATEGORY_MAP: dict[str, HintCategory] = {
    "heading": HintCategory.STRUCTURE,
    "structure": HintCategory.STRUCTURE,
    "reading order": HintCategory.STRUCTURE,
    "tag": HintCategory.STRUCTURE,
    "alt text": HintCategory.FIGURES,
    "alternative": HintCategory.FIGURES,
    "figure": HintCategory.FIGURES,
    "image": HintCategory.FIGURES,
    "table": HintCategory.TABLES,
    "header cell": HintCategory.TABLES,
    "emphasis": HintCategory.TYPOGRAPHY,
    "font": HintCategory.TYPOGRAPHY,
}


class PDFAccessibilityAnalyzer:
    """Analyzer using VeraPDF for PDF/UA-1 accessibility validation.

    Connects to a VeraPDF REST API service to validate PDFs against
    the PDF/UA-1 profile (ISO 14289-1:2014), which maps to WCAG 2.x
    accessibility requirements.

    Attributes:
        verapdf_url: Base URL for VeraPDF REST API
        timeout: Request timeout in seconds

    Example:
        >>> analyzer = PDFAccessibilityAnalyzer()
        >>> hints = await analyzer.analyze_pdf("/path/to/document.pdf")
        >>> for hint in hints:
        ...     print(f"{hint.rule_id}: {hint.message}")
    """

    def __init__(
        self,
        verapdf_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """Initialize with VeraPDF REST API URL.

        Args:
            verapdf_url: VeraPDF REST API URL (default from settings)
            timeout: Request timeout in seconds (default from settings)
        """
        self.verapdf_url = verapdf_url or settings.verapdf_url
        self.timeout = timeout or settings.verapdf_timeout
        self._client: httpx.AsyncClient | None = None
        logger.debug(
            f"PDFAccessibilityAnalyzer initialized: {self.verapdf_url} "
            f"(timeout: {self.timeout}s)"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=float(self.timeout))
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.debug("PDFAccessibilityAnalyzer: HTTP client closed")

    async def __aenter__(self) -> "PDFAccessibilityAnalyzer":
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Async context manager exit."""
        await self.close()

    async def check_health(self) -> bool:
        """Check if VeraPDF service is available.

        Returns:
            True if service is reachable, False otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.verapdf_url}/api/info")
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"VeraPDF health check failed: {e}")
            return False

    async def analyze_pdf(self, pdf_path: Path | str) -> list[IssueHint]:
        """Analyze PDF for accessibility issues using PDF/UA-1 profile.

        Args:
            pdf_path: Path to PDF file

        Returns:
            List of IssueHint objects for detected accessibility issues

        Raises:
            FileNotFoundError: If PDF file does not exist
            httpx.HTTPStatusError: If VeraPDF API returns an error
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        logger.info(f"Analyzing PDF accessibility: {pdf_path.name}")

        client = await self._get_client()

        try:
            # Submit PDF to VeraPDF REST API with PDF/UA-1 profile
            with open(pdf_path, "rb") as f:
                response = await client.post(
                    f"{self.verapdf_url}/api/validate/ua1",
                    files={"file": (pdf_path.name, f, "application/pdf")},
                    headers={"Accept": "application/json"},
                )

            response.raise_for_status()
            result = response.json()

            # Parse VeraPDF results into hints
            hints = self._parse_verapdf_results(result)

            logger.info(f"VeraPDF analysis complete: {len(hints)} issues found")
            return hints

        except httpx.HTTPStatusError as e:
            logger.error(f"VeraPDF API error: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            logger.error(f"VeraPDF request failed: {e}")
            raise
        except Exception as e:
            logger.error(f"VeraPDF analysis failed: {e}")
            raise

    def _parse_verapdf_results(self, result: dict[str, Any]) -> list[IssueHint]:
        """Parse VeraPDF JSON response into IssueHint objects.

        Args:
            result: JSON response from VeraPDF API

        Returns:
            List of IssueHint objects for failed checks
        """
        hints: list[IssueHint] = []

        # Navigate VeraPDF response structure
        # Structure: report -> jobs[0] -> validationResult -> details -> ruleSummaries
        report = result.get("report", result)  # Handle both wrapped and unwrapped
        jobs = report.get("jobs", [])

        if not jobs:
            logger.debug("No jobs in VeraPDF response")
            return hints

        job = jobs[0]
        validation_result = job.get("validationResult", {})

        # Check if compliant (if so, no hints needed)
        if validation_result.get("isCompliant", False):
            logger.debug("PDF is compliant with PDF/UA-1")
            return hints

        details = validation_result.get("details", {})
        rule_summaries = details.get("ruleSummaries", [])

        for rule_summary in rule_summaries:
            # Only process failed rules
            failed_checks = rule_summary.get("failedChecks", 0)
            if failed_checks == 0:
                continue

            # Extract rule information
            specification = rule_summary.get("specification", "")
            clause = rule_summary.get("clause", "")
            test_number = rule_summary.get("testNumber", "")
            description = rule_summary.get("description", "PDF/UA validation failure")

            # Build rule ID
            rule_id = self._build_rule_id(specification, clause, test_number)

            # Determine category
            category = self._determine_category(specification, clause, description)

            # Determine severity (all PDF/UA failures are errors by default)
            severity = HintSeverity.ERROR

            # Process individual check failures
            checks = rule_summary.get("checks", [])
            for check in checks:
                if check.get("status") != "failed":
                    continue

                context = check.get("context", "")
                page_number = self._extract_page_number(context)

                hint = IssueHint(
                    source=HintSource.VERAPDF,
                    rule_id=rule_id,
                    category=category,
                    severity=severity,
                    message=description,
                    location=context[:200] if context else "",
                    page_number=page_number,
                    context=context[:500] if context else "",
                    suggestion=self._get_suggestion(category, description),
                )
                hints.append(hint)

        return hints

    def _build_rule_id(
        self,
        specification: str,
        clause: str,
        test_number: str,
    ) -> str:
        """Build a rule ID from VeraPDF rule components.

        Args:
            specification: ISO specification (e.g., "ISO_14289-1:2014")
            clause: Clause number (e.g., "7.2")
            test_number: Test number within clause

        Returns:
            Formatted rule ID (e.g., "PDF/UA-1:7.2-1")
        """
        # Normalize specification to short form
        if "14289" in specification:
            spec_short = "PDF/UA-1"
        elif "32000" in specification:
            spec_short = "PDF"
        else:
            spec_short = specification.replace("_", "/").replace(":", "")

        parts = [spec_short]
        if clause:
            parts.append(clause)
        if test_number:
            parts.append(str(test_number))

        return ":".join(parts[:2]) + (f"-{test_number}" if test_number else "")

    def _determine_category(
        self,
        specification: str,
        clause: str,
        description: str,
    ) -> HintCategory:
        """Determine hint category from rule specification and description.

        Args:
            specification: ISO specification
            clause: Clause number
            description: Rule description

        Returns:
            HintCategory for routing to specialist agent
        """
        # Try specification mapping first
        # Sort by pattern length (longest first) so specific patterns match before general ones
        # e.g., "WCAG2AA-1-3-1-H51" should match before "WCAG2AA-1-3-1"
        sorted_patterns = sorted(
            VERAPDF_CATEGORY_MAP.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )
        for spec_pattern, category in sorted_patterns:
            if spec_pattern in specification:
                return category

        # Try clause mapping
        for clause_prefix, category in CLAUSE_CATEGORY_MAP.items():
            if clause.startswith(clause_prefix):
                return category

        # Try keyword detection in description
        description_lower = description.lower()
        for keyword, category in KEYWORD_CATEGORY_MAP.items():
            if keyword in description_lower:
                return category

        # Default to GENERAL
        return HintCategory.GENERAL

    def _extract_page_number(self, context: str) -> int | None:
        """Extract page number from VeraPDF context string.

        VeraPDF locations often include page references like:
        - "root/document[0]/Page[2]" (0-indexed)
        - "Pages/Page[1]/..."

        Args:
            context: VeraPDF context/location string

        Returns:
            1-indexed page number, or None if not found
        """
        # Match patterns like Page[N] or page[N]
        match = re.search(r"[Pp]age\[(\d+)\]", context)
        if match:
            # Convert 0-indexed to 1-indexed
            return int(match.group(1)) + 1
        return None

    def _get_suggestion(self, category: HintCategory, description: str) -> str:
        """Get actionable suggestion based on category and description.

        Args:
            category: Hint category
            description: Rule description

        Returns:
            Actionable suggestion for fixing the issue
        """
        suggestions = {
            HintCategory.STRUCTURE: (
                "Ensure proper document structure with tagged content "
                "and logical reading order"
            ),
            HintCategory.FIGURES: (
                "Add meaningful alternative text to images and figures"
            ),
            HintCategory.TABLES: (
                "Ensure tables have proper header cells and structure"
            ),
            HintCategory.TYPOGRAPHY: (
                "Use semantic markup for emphasis and styling"
            ),
            HintCategory.GENERAL: (
                "Review PDF/UA-1 compliance requirements"
            ),
        }

        # Check for specific patterns in description
        description_lower = description.lower()
        if "alt" in description_lower or "alternative" in description_lower:
            return "Add meaningful alternative text describing the content"
        if "heading" in description_lower:
            return "Ensure proper heading structure and hierarchy"
        if "reading order" in description_lower:
            return "Fix logical reading order of content"
        if "table" in description_lower and "header" in description_lower:
            return "Mark header cells as TH elements in table structure"

        return suggestions.get(category, "Review and fix accessibility issue")
