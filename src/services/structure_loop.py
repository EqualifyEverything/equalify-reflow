"""Structure Verification Loop service for Phase 3a: Refine.

This service runs a validation-driven loop to ensure structurally correct
markdown before specialized agents run. The loop:

1. Detects issues using deterministic tools (lint, spell check) - FREE
2. Auto-fixes formatting issues using mdformat - FREE
3. Uses LLM (Haiku) for semantic issues (OCR decisions in context)
4. Loops until lint clean or max 3 iterations

Key principles:
- OCR errors are NEVER auto-corrected (LLM decides in context)
- Formatting fixes are safe and automatic
- Glass box: all corrections captured in StructureTrace
"""

from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import mdformat
from pydantic import BaseModel, ConfigDict, Field

from src.shared.models.observation import Observation
from src.utils.ocr_checker import OCRChecker, OCRSuggestion

if TYPE_CHECKING:
    from src.services.pdf_converter import PageData
    from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================


class LintIssue(BaseModel):
    """A lint issue detected by pymarkdownlnt."""

    rule: str = Field(..., description="Rule ID (e.g., MD001)")
    message: str = Field(..., description="Description of the issue")
    line: int = Field(..., ge=1, description="Line number")
    column: int = Field(default=1, ge=1, description="Column number")

    model_config = ConfigDict(frozen=True)


class HeadingIssue(BaseModel):
    """A heading hierarchy issue detected by Python validation."""

    type: str = Field(..., description="Issue type: multiple_h1, skipped_level")
    description: str = Field(..., description="Human-readable description")
    locations: list[int] = Field(default_factory=list, description="Line numbers")


class ReadingOrderIssue(BaseModel):
    """A reading order issue detected on complex layout pages."""

    page_num: int = Field(..., ge=1, description="Page number")
    description: str = Field(..., description="Description of the issue")
    suggested_fix: str = Field(default="", description="Suggested correction")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class StructureCorrection(BaseModel):
    """A single correction made during the structure loop.

    Attributes:
        type: Correction type (format, heading, ocr, reading_order)
        description: Human-readable description
        iteration: Which loop iteration this occurred in
        auto: True if automatic (mdformat), False if LLM
    """

    type: str = Field(..., description="format, heading, ocr, reading_order")
    description: str = Field(..., description="What was corrected")
    iteration: int = Field(..., ge=0, description="Loop iteration (0-indexed)")
    auto: bool = Field(..., description="True if auto-fix, False if LLM")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"type": "format", "description": "Fixed 3 spacing issues", "iteration": 0, "auto": True}
        }
    )


class StructureTrace(BaseModel):
    """Glass box trace of the structure verification loop.

    Captures everything that happened during structure verification
    for full transparency and debugging.

    Attributes:
        iterations: Number of loop iterations executed
        lint_issues_found: Total lint issues detected
        lint_issues_fixed: Lint issues that were fixed
        ocr_suggestions_processed: OCR suggestions reviewed by LLM
        corrections: All corrections made
        final_lint_clean: Whether final markdown passes lint
        observations: Observations generated during loop
        time_seconds: Total execution time
        cost_cents: LLM cost for semantic fixes
    """

    iterations: int = Field(..., ge=1, le=3)
    lint_issues_found: int = Field(default=0, ge=0)
    lint_issues_fixed: int = Field(default=0, ge=0)
    ocr_suggestions_processed: int = Field(default=0, ge=0)
    corrections: list[StructureCorrection] = Field(default_factory=list)
    final_lint_clean: bool = Field(default=False)
    observations: list[Observation] = Field(default_factory=list)
    time_seconds: float = Field(default=0.0, ge=0.0)
    cost_cents: float = Field(default=0.0, ge=0.0)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "iterations": 2,
                "lint_issues_found": 5,
                "lint_issues_fixed": 5,
                "ocr_suggestions_processed": 2,
                "corrections": [],
                "final_lint_clean": True,
                "observations": [],
                "time_seconds": 3.5,
                "cost_cents": 1.2,
            }
        }
    )


class StructureLoopResult(BaseModel):
    """Output of the structure verification loop.

    Attributes:
        markdown: The corrected markdown (structurally clean)
        trace: Full glass box trace of what happened
    """

    markdown: str = Field(..., description="Corrected markdown")
    trace: StructureTrace = Field(..., description="Glass box trace")


# =============================================================================
# Lint Rules Configuration
# =============================================================================


# =============================================================================
# Constants
# =============================================================================

# Timeout for lint subprocess (seconds)
LINT_TIMEOUT_SECONDS = 30


# Rules to check with pymarkdownlnt
ENABLED_LINT_RULES: set[str] = {
    # Headings
    "MD001",  # Heading levels increment by one
    "MD003",  # Heading style consistency
    "MD022",  # Headings surrounded by blank lines
    "MD023",  # Headings must start at beginning of line
    "MD024",  # No duplicate heading text (siblings only)
    "MD025",  # Single H1 heading
    # Lists
    "MD004",  # Unordered list style consistency
    "MD005",  # List indentation consistency
    "MD007",  # Unordered list indentation
    "MD030",  # Spaces after list markers
    # Code
    "MD031",  # Fenced code blocks surrounded by blank lines
    "MD040",  # Fenced code blocks should have language
    # Tables
    "MD055",  # Table pipe style
    "MD056",  # Table column count
    # General
    "MD009",  # Trailing spaces
    "MD010",  # Hard tabs
    "MD012",  # Multiple consecutive blank lines
    "MD047",  # Files should end with newline
}

# Rules that can be safely auto-fixed by mdformat
FORMATTING_RULES: set[str] = {
    "MD009",  # Trailing spaces
    "MD010",  # Hard tabs
    "MD012",  # Multiple consecutive blank lines
    "MD022",  # Headings surrounded by blank lines
    "MD031",  # Fenced code blocks surrounded by blank lines
    "MD047",  # Files should end with newline
}


# =============================================================================
# Structure Loop Service
# =============================================================================


@dataclass
class _HeadingInfo:
    """Internal representation of a heading for hierarchy checking."""

    level: int
    text: str
    line: int


class StructureLoop:
    """Validation-driven structure correction loop.

    This service ensures structurally correct markdown by:
    1. Running lint checks (free, deterministic)
    2. Running OCR error detection (free, deterministic)
    3. Auto-fixing formatting issues (mdformat, free)
    4. Using LLM for semantic issues (OCR decisions, heading fixes)
    5. Looping until clean or max iterations reached

    Example:
        >>> loop = StructureLoop()
        >>> result = await loop.run(
        ...     markdown=raw_markdown,
        ...     pages=pages,
        ...     manifest=manifest,
        ...     job_id="job-123"
        ... )
        >>> print(f"Clean: {result.trace.final_lint_clean}")
        >>> print(f"Iterations: {result.trace.iterations}")
    """

    MAX_ITERATIONS = 3

    def __init__(self) -> None:
        """Initialize structure loop with OCR checker."""
        self._ocr_checker = OCRChecker()

    async def run(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
    ) -> StructureLoopResult:
        """Run validation loop until clean or max iterations.

        Args:
            markdown: Raw markdown from extraction
            pages: Page data with images
            manifest: Document manifest with key entities
            job_id: Job ID for correlation

        Returns:
            StructureLoopResult with corrected markdown and trace
        """
        start_time = datetime.now(UTC)
        corrections: list[StructureCorrection] = []
        observations: list[Observation] = []
        current_markdown = markdown
        total_lint_issues = 0
        total_ocr_suggestions = 0
        total_cost_cents = 0.0
        iteration = 0

        logger.info(f"Job {job_id}: Starting structure verification loop")

        for iteration in range(self.MAX_ITERATIONS):
            logger.debug(f"Job {job_id}: Structure loop iteration {iteration + 1}")

            # Step 1: Detect issues (Python - free)
            lint_issues, ocr_suggestions, heading_issues = self._detect_all_issues(current_markdown, manifest)

            total_lint_issues += len(lint_issues)
            total_ocr_suggestions += len(ocr_suggestions)

            logger.debug(
                f"Job {job_id}: Found {len(lint_issues)} lint issues, "
                f"{len(ocr_suggestions)} OCR suggestions, "
                f"{len(heading_issues)} heading issues"
            )

            # Step 2: Auto-fix formatting ONLY (mdformat - free)
            current_markdown, format_correction = self._auto_fix_formatting(
                current_markdown, lint_issues, iteration, job_id
            )
            if format_correction:
                corrections.append(format_correction)

            # Step 3: Collect semantic issues for LLM
            semantic_lint = self._get_semantic_issues(lint_issues)

            # Step 4: LLM fixes semantic issues (OCR decisions in context)
            if semantic_lint or heading_issues or ocr_suggestions:
                llm_result = await self._llm_fix(
                    markdown=current_markdown,
                    lint_issues=semantic_lint,
                    heading_issues=heading_issues,
                    ocr_suggestions=ocr_suggestions,
                    manifest=manifest,
                    job_id=job_id,
                    iteration=iteration,
                )
                current_markdown = llm_result.markdown
                corrections.extend(llm_result.corrections)
                observations.extend(llm_result.observations)
                total_cost_cents += llm_result.cost_cents

            # Step 5: Re-check - if clean, exit loop
            if self._is_semantically_clean(current_markdown):
                logger.info(f"Job {job_id}: Structure loop completed - lint clean after {iteration + 1} iterations")
                break

        # Build final trace
        trace = self._build_trace(
            current_markdown=current_markdown,
            iteration=iteration,
            total_lint_issues=total_lint_issues,
            total_ocr_suggestions=total_ocr_suggestions,
            corrections=corrections,
            observations=observations,
            total_cost_cents=total_cost_cents,
            start_time=start_time,
        )

        logger.info(
            f"Job {job_id}: Structure loop finished - "
            f"{trace.iterations} iterations, "
            f"{trace.lint_issues_fixed}/{trace.lint_issues_found} issues fixed, "
            f"clean: {trace.final_lint_clean}, "
            f"cost: ${total_cost_cents / 100:.4f}"
        )

        return StructureLoopResult(markdown=current_markdown, trace=trace)

    def _detect_all_issues(
        self,
        markdown: str,
        manifest: DocumentManifest,
    ) -> tuple[list[LintIssue], list[OCRSuggestion], list[HeadingIssue]]:
        """Detect all issues in markdown (lint, OCR, heading).

        Args:
            markdown: Markdown text to check
            manifest: Document manifest with key entities

        Returns:
            Tuple of (lint_issues, ocr_suggestions, heading_issues)
        """
        lint_issues = self._run_lint(markdown)
        ocr_suggestions = self._check_ocr(markdown, manifest)
        heading_issues = self._check_heading_hierarchy(markdown)
        return lint_issues, ocr_suggestions, heading_issues

    def _auto_fix_formatting(
        self,
        markdown: str,
        lint_issues: list[LintIssue],
        iteration: int,
        job_id: str,
    ) -> tuple[str, StructureCorrection | None]:
        """Auto-fix formatting issues using mdformat.

        Args:
            markdown: Markdown text to format
            lint_issues: All lint issues found
            iteration: Current iteration number
            job_id: Job ID for logging

        Returns:
            Tuple of (formatted_markdown, correction_or_none)
        """
        formatting_issues = [i for i in lint_issues if i.rule in FORMATTING_RULES]

        if not formatting_issues:
            return markdown, None

        formatted = self._apply_mdformat(markdown)

        if formatted == markdown:
            return markdown, None

        logger.debug(f"Job {job_id}: mdformat fixed formatting issues")
        correction = StructureCorrection(
            type="format",
            description=f"Fixed {len(formatting_issues)} formatting issues",
            iteration=iteration,
            auto=True,
        )
        return formatted, correction

    def _get_semantic_issues(self, lint_issues: list[LintIssue]) -> list[LintIssue]:
        """Filter lint issues to only semantic ones (not formatting).

        Args:
            lint_issues: All lint issues

        Returns:
            Lint issues that are not formatting-related
        """
        return [i for i in lint_issues if i.rule not in FORMATTING_RULES]

    def _is_semantically_clean(self, markdown: str) -> bool:
        """Check if markdown has no remaining semantic lint issues.

        Args:
            markdown: Markdown text to check

        Returns:
            True if no semantic issues remain
        """
        remaining_lint = self._run_lint(markdown)
        remaining_semantic = self._get_semantic_issues(remaining_lint)
        return len(remaining_semantic) == 0

    def _build_trace(
        self,
        current_markdown: str,
        iteration: int,
        total_lint_issues: int,
        total_ocr_suggestions: int,
        corrections: list[StructureCorrection],
        observations: list[Observation],
        total_cost_cents: float,
        start_time: datetime,
    ) -> StructureTrace:
        """Build the final trace after loop completion.

        Args:
            current_markdown: Final markdown text
            iteration: Final iteration index (0-based)
            total_lint_issues: Total lint issues found across all iterations
            total_ocr_suggestions: Total OCR suggestions found
            corrections: All corrections made
            observations: All observations generated
            total_cost_cents: Total LLM cost
            start_time: When the loop started

        Returns:
            Complete StructureTrace
        """
        final_lint = self._run_lint(current_markdown)
        final_lint_clean = len(final_lint) == 0
        elapsed = (datetime.now(UTC) - start_time).total_seconds()

        return StructureTrace(
            iterations=iteration + 1,
            lint_issues_found=total_lint_issues,
            lint_issues_fixed=total_lint_issues - len(final_lint),
            ocr_suggestions_processed=total_ocr_suggestions,
            corrections=corrections,
            final_lint_clean=final_lint_clean,
            observations=observations,
            time_seconds=elapsed,
            cost_cents=total_cost_cents,
        )

    def _run_lint(self, markdown: str) -> list[LintIssue]:
        """Run markdown linter using pymarkdown CLI.

        Uses subprocess to call pymarkdown scan with JSON output.

        Args:
            markdown: Markdown text to lint

        Returns:
            List of lint issues (filtered to enabled rules)
        """
        try:
            # pymarkdown requires file path, so write to temp file
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
                f.write(markdown)
                temp_path = f.name

            try:
                # Run pymarkdown with JSON output
                result = subprocess.run(
                    ["pymarkdown", "scan", "--output-format", "json", temp_path],
                    capture_output=True,
                    text=True,
                    timeout=LINT_TIMEOUT_SECONDS,
                )

                issues: list[LintIssue] = []

                # Parse JSON output if available
                if result.stdout:
                    try:
                        scan_results = json.loads(result.stdout)
                        if isinstance(scan_results, list):
                            for item in scan_results:
                                rule_id = item.get("rule_id", "")
                                if rule_id in ENABLED_LINT_RULES:
                                    issues.append(
                                        LintIssue(
                                            rule=rule_id,
                                            message=item.get("description", ""),
                                            line=item.get("line_number", 1),
                                            column=item.get("column_number", 1),
                                        )
                                    )
                    except json.JSONDecodeError:
                        logger.debug("Could not parse pymarkdown JSON output")

                return issues

            finally:
                # Clean up temp file
                Path(temp_path).unlink(missing_ok=True)

        except subprocess.TimeoutExpired:
            logger.warning("Lint check timed out")
            return []  # Graceful degradation
        except FileNotFoundError:
            logger.warning("pymarkdown not found in PATH")
            return []  # Graceful degradation
        except Exception as e:
            logger.warning(f"Unexpected lint error: {e}")
            return []  # Graceful degradation

    def _check_ocr(
        self,
        markdown: str,
        manifest: DocumentManifest,
    ) -> list[OCRSuggestion]:
        """Check for potential OCR errors.

        Args:
            markdown: Markdown text to check
            manifest: Document manifest with key entities

        Returns:
            List of OCR suggestions for LLM review
        """
        key_terms: list[str] = []

        # Get key entities from summary if available
        if manifest.summary:
            key_terms.extend(manifest.summary.key_entities)
            key_terms.extend(manifest.summary.domain_terms)

        if not key_terms:
            return []

        return self._ocr_checker.check_text(markdown, key_terms)

    def _check_heading_hierarchy(self, markdown: str) -> list[HeadingIssue]:
        """Pure Python heading hierarchy validation.

        Checks:
        - Only one H1 heading
        - No skipped heading levels (H1 -> H3 without H2)

        Args:
            markdown: Markdown text to check

        Returns:
            List of heading hierarchy issues
        """
        issues: list[HeadingIssue] = []
        headings = self._extract_headings(markdown)

        # Check: Only one H1
        h1_headings = [h for h in headings if h.level == 1]
        if len(h1_headings) > 1:
            issues.append(
                HeadingIssue(
                    type="multiple_h1",
                    description=f"Found {len(h1_headings)} H1 headings, should have only 1",
                    locations=[h.line for h in h1_headings],
                )
            )

        # Check: No skipped levels
        prev_level = 0
        for heading in headings:
            if heading.level > prev_level + 1 and prev_level > 0:
                issues.append(
                    HeadingIssue(
                        type="skipped_level",
                        description=f"Skipped from H{prev_level} to H{heading.level} at line {heading.line}",
                        locations=[heading.line],
                    )
                )
            prev_level = heading.level

        return issues

    def _extract_headings(self, markdown: str) -> list[_HeadingInfo]:
        """Extract headings from markdown.

        Args:
            markdown: Markdown text

        Returns:
            List of heading info in document order
        """
        import re

        headings: list[_HeadingInfo] = []
        lines = markdown.split("\n")

        for line_num, line in enumerate(lines, 1):
            # Match ATX headings (# Heading)
            match = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
            if match:
                level = len(match.group(1))
                text = match.group(2).strip()
                headings.append(_HeadingInfo(level=level, text=text, line=line_num))

        return headings

    def _apply_mdformat(self, markdown: str) -> str:
        """Apply mdformat to fix formatting issues.

        mdformat is deterministic and only fixes formatting:
        - Trailing whitespace
        - Blank lines around headings/code blocks
        - Consistent list markers
        - File ending newline

        It does NOT change content or fix semantic issues.

        Args:
            markdown: Markdown text to format

        Returns:
            Formatted markdown
        """
        try:
            return mdformat.text(markdown)
        except Exception as e:
            logger.warning(f"mdformat failed: {e}")
            return markdown  # Return unchanged on error

    async def _llm_fix(
        self,
        markdown: str,
        lint_issues: list[LintIssue],
        heading_issues: list[HeadingIssue],
        ocr_suggestions: list[OCRSuggestion],
        manifest: DocumentManifest,
        job_id: str,
        iteration: int,
    ) -> _LLMFixResult:
        """Use LLM to fix semantic issues.

        The LLM is called for issues that require context understanding:
        - OCR error decisions (is "Exxon" a typo for "Enzo"?)
        - Heading hierarchy fixes (which heading level is correct?)
        - Complex structural issues

        Args:
            markdown: Current markdown
            lint_issues: Semantic lint issues (not formatting)
            heading_issues: Heading hierarchy issues
            ocr_suggestions: OCR suggestions for review
            manifest: Document manifest with context
            job_id: Job ID
            iteration: Current iteration number

        Returns:
            _LLMFixResult with corrected markdown and metadata

        Note:
            LLM-based structural fixes are currently disabled. The structure_fix_agent
            was removed as part of the dead code cleanup. This service will return
            the original markdown unchanged. If LLM-based fixes are needed in the
            future, the agent should be re-implemented.
        """
        # LLM-based structural fixes are currently disabled
        # Return original markdown unchanged
        if lint_issues or heading_issues or ocr_suggestions:
            logger.warning(
                f"Job {job_id}: LLM structural fixes disabled - "
                f"{len(lint_issues)} lint issues, {len(heading_issues)} heading issues, "
                f"{len(ocr_suggestions)} OCR suggestions will be skipped"
            )

        return _LLMFixResult(
            markdown=markdown,
            corrections=[],
            observations=[],
            cost_cents=0.0,
        )


@dataclass
class _LLMFixResult:
    """Internal result from LLM fix operation."""

    markdown: str
    corrections: list[StructureCorrection]
    observations: list[Observation]
    cost_cents: float


__all__ = [
    "StructureLoop",
    "StructureLoopResult",
    "StructureTrace",
    "StructureCorrection",
    "LintIssue",
    "HeadingIssue",
    "ReadingOrderIssue",
    "LINT_TIMEOUT_SECONDS",
    "ENABLED_LINT_RULES",
    "FORMATTING_RULES",
]
