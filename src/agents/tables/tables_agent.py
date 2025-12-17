"""Tables agent for Phase 3b: Refine (Specialized Agents).

Merged tables agent that combines structure analysis and accuracy validation
into a single agent with a validation loop.

Key changes from previous approach:
- Single agent replaces structure_agent + accuracy_agent
- Outputs AgentResult directly (no consolidation layer)
- Python validation loop (max 2 iterations) for table structure
- High confidence (>=0.95) + valid → AutoCorrection
- Low confidence or issues → ReviewItem

Reference: PRD-024 - Tables Agent Merge & Validation Loop
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.services.pdf_converter import PageData
from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest
from src.shared.models.review_checklist import ReviewItem, ReviewOption

logger = logging.getLogger(__name__)

_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost-effective table enhancement


# =============================================================================
# Models
# =============================================================================


class TablePlaceholder(BaseModel):
    """Parsed table placeholder from markdown.

    Attributes:
        id: Unique identifier (e.g., "p3:t1")
        full_match: Full <!-- TABLE:... --> block including content
        start_marker: Opening marker (e.g., "<!-- TABLE:p3:t1 -->")
        end_marker: Closing marker (e.g., "<!-- /TABLE:p3:t1 -->")
        content: The table markdown between markers
        page_num: Page number from marker
        table_index: Table index on page from marker
    """

    id: str = Field(description="Unique identifier (e.g., 'p3:t1')")
    full_match: str = Field(description="Full <!-- TABLE:... --> block")
    start_marker: str = Field(description="Opening marker")
    end_marker: str = Field(description="Closing marker")
    content: str = Field(description="Table markdown between markers")
    page_num: int = Field(ge=1, description="Page number")
    table_index: int = Field(ge=1, description="Table index on page")


class TableValidationIssue(BaseModel):
    """Issue found during table validation.

    Attributes:
        type: Issue type identifier
        description: Human-readable description
        severity: Impact severity (critical, major, minor)
    """

    type: str = Field(description="Issue type identifier")
    description: str = Field(description="Human-readable description")
    severity: str = Field(description="critical, major, or minor")


class TableEnhanceOutput(BaseModel):
    """Output from table enhancement LLM call.

    Attributes:
        table_markdown: Enhanced table in markdown format
        reasoning: Explanation of changes made
        confidence: Confidence in the enhancement (0.0-1.0)
        changes_made: List of specific changes applied
    """

    table_markdown: str = Field(description="Enhanced table markdown")
    reasoning: str = Field(description="Explanation of changes")
    confidence: float = Field(ge=0.0, le=1.0, description="Enhancement confidence")
    changes_made: list[str] = Field(
        default_factory=list, description="Specific changes applied"
    )


# =============================================================================
# Agent
# =============================================================================


class TablesAgent:
    """Merged tables agent with validation loop.

    Orchestrates table enhancement with:
    1. Find table placeholders in markdown
    2. For each table:
       a. Enhance using LLM (compare to visual)
       b. Validate structure (Python)
       c. Loop if validation fails (max 2 iterations)
       d. Route based on confidence and validation

    Attributes:
        MAX_ITERATIONS: Maximum validation loop iterations (2)
        CONFIDENCE_THRESHOLD: Minimum confidence for auto-correction (0.95)
    """

    MAX_ITERATIONS = 2
    CONFIDENCE_THRESHOLD = 0.95

    def __init__(self) -> None:
        """Initialize TablesAgent with lazy agent creation."""
        self._agent: Agent[None, TableEnhanceOutput] | None = None
        self._prompts: dict | None = None

    def _get_prompts(self) -> dict:
        """Load prompts from YAML (cached)."""
        if self._prompts is None:
            self._prompts = load_prompts("tables_enhance.yaml")
        return self._prompts

    def _get_agent(self) -> Agent[None, TableEnhanceOutput]:
        """Get or create the enhancement agent (cached)."""
        if self._agent is None:
            self._agent = create_agent(
                prompts_file="tables_enhance.yaml",
                output_type=TableEnhanceOutput,
                model_tier=_MODEL_TIER,
                use_deps=False,
                max_retries=2,
            )
        return self._agent

    async def process(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        job_id: str,
    ) -> AgentResult:
        """Process all tables and return unified result.

        Args:
            markdown: Full document markdown with table placeholders
            pages: List of PageData with page images
            manifest: Document manifest with metadata
            job_id: Job identifier for tracking

        Returns:
            AgentResult containing observations, auto_corrections, and review_items
        """
        start_time = datetime.now(UTC)
        observations: list[Observation] = []
        auto_corrections: list[AutoCorrection] = []
        review_items: list[ReviewItem] = []
        enhanced_content: dict[str, str] = {}
        total_cost = 0.0
        total_iterations = 0

        # Find all table placeholders
        placeholders = self._find_table_placeholders(markdown)

        if not placeholders:
            return AgentResult(
                agent_name="tables",
                observations=[],
                auto_corrections=[],
                review_items=[],
                reasoning_summary="No table placeholders found.",
                confidence=1.0,
                enhanced_content=None,
                cost_cents=0.0,
                time_seconds=0.0,
            )

        logger.info(
            f"Job {job_id}: TablesAgent found {len(placeholders)} table placeholders"
        )

        # Process each table with validation loop
        for placeholder in placeholders:
            # Get page image
            page_data = self._get_page_data(pages, placeholder.page_num)
            if not page_data or not page_data.image_base64:
                logger.warning(
                    f"Job {job_id}: No image data for page {placeholder.page_num}, "
                    f"skipping table {placeholder.id}"
                )
                continue

            try:
                # Process table with validation loop
                result = await self._process_single_table(
                    placeholder=placeholder,
                    page_image=page_data.image_base64,
                    manifest=manifest,
                    job_id=job_id,
                )

                total_cost += result["cost"]
                total_iterations += result["iterations"]

                # Create observation
                obs = self._create_observation(
                    placeholder=placeholder,
                    confidence=result["confidence"],
                    job_id=job_id,
                )
                observations.append(obs)

                # Route based on confidence and validation
                self._route_table(
                    placeholder=placeholder,
                    enhanced_table=result["enhanced_table"],
                    validation_issues=result["validation_issues"],
                    confidence=result["confidence"],
                    reasoning=result["reasoning"],
                    iterations=result["iterations"],
                    observation=obs,
                    auto_corrections=auto_corrections,
                    review_items=review_items,
                    enhanced_content=enhanced_content,
                )

            except Exception as e:
                logger.error(
                    f"Job {job_id}: TablesAgent failed on table {placeholder.id}: {e}",
                    exc_info=True,
                )
                # Create observation for failed table
                obs = Observation(
                    id=str(uuid4()),
                    job_id=job_id,
                    agent="tables",
                    source="agent",
                    visual_description=f"Table on page {placeholder.page_num} (processing failed)",
                    markup_description=f"Table with {self._count_rows(placeholder.content)} rows",
                    location=ObservationLocation(
                        location_type="element",
                        value=f"table[data-id='{placeholder.id}']",
                        page_num=placeholder.page_num,
                    ),
                    confidence=0.0,
                    severity="major",
                    category="table",
                )
                observations.append(obs)

                # Create review item for manual processing
                review_items.append(
                    ReviewItem(
                        id=str(uuid4()),
                        observation_id=obs.id,
                        agent="tables",
                        question=f"Verify table on page {placeholder.page_num} (processing failed)",
                        options=[
                            ReviewOption(
                                id="keep_original",
                                label="Keep original table",
                                action="keep",
                                is_recommended=True,
                            ),
                            ReviewOption(
                                id="other",
                                label="Edit table manually",
                                action="other",
                                is_recommended=False,
                            ),
                        ],
                        search_text=placeholder.full_match,
                        context=(
                            f"**Error during processing:**\n{str(e)}\n\n"
                            f"**Original table:**\n```\n{placeholder.content}\n```"
                        ),
                        page_num=placeholder.page_num,
                        agent_recommendation="Manual review required due to processing error",
                        agent_confidence=0.0,
                    )
                )

        end_time = datetime.now(UTC)

        return AgentResult(
            agent_name="tables",
            observations=observations,
            auto_corrections=auto_corrections,
            review_items=review_items,
            reasoning_summary=self._build_summary(
                placeholders, auto_corrections, review_items
            ),
            confidence=self._calculate_confidence(auto_corrections, review_items),
            enhanced_content=enhanced_content if enhanced_content else None,
            cost_cents=total_cost,
            time_seconds=(end_time - start_time).total_seconds(),
            iterations=max(1, total_iterations // len(placeholders)) if placeholders else 1,
        )

    async def _process_single_table(
        self,
        placeholder: TablePlaceholder,
        page_image: str,
        manifest: DocumentManifest,
        job_id: str,
    ) -> dict:
        """Process a single table with validation loop.

        Args:
            placeholder: Table placeholder to process
            page_image: Base64-encoded page image
            manifest: Document manifest
            job_id: Job identifier

        Returns:
            Dict with enhanced_table, validation_issues, confidence, reasoning,
            iterations, and cost
        """
        current_table = placeholder.content
        final_result: TableEnhanceOutput | None = None
        validation_issues: list[TableValidationIssue] = []
        total_cost = 0.0
        iterations_used = 0

        # Validation loop
        for iteration in range(self.MAX_ITERATIONS):
            iterations_used = iteration + 1

            # Enhance table
            result, usage = await self._enhance_table(
                table_markdown=current_table,
                page_image=page_image,
                page_num=placeholder.page_num,
                job_id=job_id,
            )
            total_cost += usage.estimated_cost_cents
            final_result = result

            enhanced_table = result.table_markdown

            # Validate table structure
            validation_issues = self._validate_table(enhanced_table)

            if not validation_issues:
                logger.debug(
                    f"Job {job_id}: Table {placeholder.id} validated successfully "
                    f"after {iterations_used} iteration(s)"
                )
                break  # Valid - exit loop

            # Has issues - prepare for next iteration
            logger.debug(
                f"Job {job_id}: Table {placeholder.id} has {len(validation_issues)} "
                f"validation issues, iteration {iterations_used}/{self.MAX_ITERATIONS}"
            )
            current_table = enhanced_table

        return {
            "enhanced_table": final_result.table_markdown if final_result else placeholder.content,
            "validation_issues": validation_issues,
            "confidence": final_result.confidence if final_result else 0.5,
            "reasoning": final_result.reasoning if final_result else "No enhancement performed",
            "iterations": iterations_used,
            "cost": total_cost,
        }

    async def _enhance_table(
        self,
        table_markdown: str,
        page_image: str,
        page_num: int,
        job_id: str,
    ) -> tuple[TableEnhanceOutput, LLMUsage]:
        """Enhance a single table using LLM.

        Args:
            table_markdown: Current table markdown
            page_image: Base64-encoded page image
            page_num: Page number
            job_id: Job identifier

        Returns:
            Tuple of (TableEnhanceOutput, LLMUsage)
        """
        agent = self._get_agent()
        prompts = self._get_prompts()

        # Build user prompt
        user_prompt = prompts["user_prompt"].format(
            page_num=page_num,
            table_markdown=table_markdown,
        )

        # Decode image for multimodal input
        image_bytes = base64.b64decode(page_image)

        # Build multimodal prompt
        prompt: list = [
            user_prompt,
            BinaryContent(data=image_bytes, media_type="image/png"),
        ]

        logger.debug(f"Job {job_id}: Enhancing table on page {page_num}")

        result = await run_agent(
            agent=agent,
            prompt=prompt,
            job_id=job_id,
            agent_name="tables_enhance",
            model_tier=_MODEL_TIER,
            system_prompt=prompts["system_prompt"],
        )

        output: TableEnhanceOutput = result.output
        usage = extract_usage(result, _MODEL_TIER)

        logger.debug(
            f"Job {job_id}: Table enhancement complete - "
            f"confidence: {output.confidence:.2f}, "
            f"cost: ${usage.estimated_cost_cents/100:.4f}"
        )

        return output, usage

    def _find_table_placeholders(self, markdown: str) -> list[TablePlaceholder]:
        """Find all table placeholders in markdown.

        Matches patterns: <!-- TABLE:pN:tM --> ... <!-- /TABLE:pN:tM -->

        Args:
            markdown: Full document markdown

        Returns:
            List of TablePlaceholder objects
        """
        placeholders: list[TablePlaceholder] = []

        # Match: <!-- TABLE:pN:tM --> ... <!-- /TABLE:pN:tM -->
        pattern = r"(<!-- TABLE:p(\d+):t(\d+) -->)(.*?)(<!-- /TABLE:p\2:t\3 -->)"

        for match in re.finditer(pattern, markdown, re.DOTALL):
            start_marker = match.group(1)
            page_num = int(match.group(2))
            table_index = int(match.group(3))
            content = match.group(4).strip()
            end_marker = match.group(5)

            placeholders.append(
                TablePlaceholder(
                    id=f"p{page_num}:t{table_index}",
                    full_match=match.group(0),
                    start_marker=start_marker,
                    end_marker=end_marker,
                    content=content,
                    page_num=page_num,
                    table_index=table_index,
                )
            )

        return placeholders

    def _validate_table(self, table_markdown: str) -> list[TableValidationIssue]:
        """Validate table structure (pure Python).

        Checks:
        - Minimum row count (at least 2 rows)
        - Column consistency across rows
        - Header separator presence (|---|---|)
        - Non-empty table (has actual content)

        Args:
            table_markdown: Table markdown to validate

        Returns:
            List of validation issues (empty if valid)
        """
        issues: list[TableValidationIssue] = []

        lines = [line for line in table_markdown.strip().split("\n") if line.strip()]

        if len(lines) < 2:
            issues.append(
                TableValidationIssue(
                    type="too_few_rows",
                    description="Table has fewer than 2 rows",
                    severity="critical",
                )
            )
            return issues

        # Check column consistency
        col_counts: list[int] = []
        for line in lines:
            if "|" in line and not re.match(r"^\s*\|[-:\s|]+\|\s*$", line):
                # Count columns (pipes minus edge pipes)
                cols = line.count("|")
                if line.strip().startswith("|"):
                    cols -= 1
                if line.strip().endswith("|"):
                    cols -= 1
                col_counts.append(cols)

        if col_counts and len(set(col_counts)) > 1:
            issues.append(
                TableValidationIssue(
                    type="inconsistent_columns",
                    description=f"Column counts vary: {col_counts}",
                    severity="major",
                )
            )

        # Check for header separator
        if len(lines) > 1:
            second_line = lines[1].strip()
            if not re.match(r"^\|[-:\s|]+\|$", second_line):
                issues.append(
                    TableValidationIssue(
                        type="missing_header_separator",
                        description="Missing header separator row (|---|---|)",
                        severity="major",
                    )
                )

        # Check for empty cells (all cells empty)
        cell_content = re.findall(r"\|([^|]+)\|", table_markdown)
        non_empty = [c for c in cell_content if c.strip() and c.strip() != "-"]
        if not non_empty:
            issues.append(
                TableValidationIssue(
                    type="empty_table",
                    description="All cells are empty",
                    severity="critical",
                )
            )

        return issues

    def _get_page_data(
        self, pages: list[PageData], page_num: int
    ) -> PageData | None:
        """Get PageData for a specific page number.

        Args:
            pages: List of all page data
            page_num: Target page number (1-indexed)

        Returns:
            PageData for the page, or None if not found
        """
        for page in pages:
            if page.page_num == page_num:
                return page
        return None

    def _count_rows(self, table_markdown: str) -> int:
        """Count data rows in table.

        Args:
            table_markdown: Table markdown

        Returns:
            Number of data rows (excluding header separator)
        """
        lines = [line for line in table_markdown.strip().split("\n") if "|" in line]
        # Subtract header separator
        return max(0, len(lines) - 1)

    def _create_observation(
        self,
        placeholder: TablePlaceholder,
        confidence: float,
        job_id: str,
    ) -> Observation:
        """Create an Observation for a table.

        Args:
            placeholder: Table placeholder
            confidence: Confidence score
            job_id: Job identifier

        Returns:
            Observation for this table
        """
        return Observation(
            id=str(uuid4()),
            job_id=job_id,
            agent="tables",
            source="agent",
            visual_description=f"Table on page {placeholder.page_num}",
            markup_description=f"Table with {self._count_rows(placeholder.content)} rows",
            location=ObservationLocation(
                location_type="element",
                value=f"table[data-id='{placeholder.id}']",
                page_num=placeholder.page_num,
            ),
            confidence=confidence,
            severity="major",
            category="table",
        )

    def _route_table(
        self,
        placeholder: TablePlaceholder,
        enhanced_table: str,
        validation_issues: list[TableValidationIssue],
        confidence: float,
        reasoning: str,
        iterations: int,
        observation: Observation,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
        enhanced_content: dict[str, str],
    ) -> None:
        """Route a table to auto_corrections or review_items.

        Routing rules:
        - High confidence (>=0.95) + no validation issues → AutoCorrection
        - Low confidence or validation issues → ReviewItem

        Args:
            placeholder: Table placeholder
            enhanced_table: Enhanced table markdown
            validation_issues: Validation issues found
            confidence: Enhancement confidence
            reasoning: Enhancement reasoning
            iterations: Number of iterations used
            observation: Observation for this table
            auto_corrections: List to append auto-corrections to
            review_items: List to append review items to
            enhanced_content: Dict to update with enhanced content
        """
        new_content = (
            f"{placeholder.start_marker}\n{enhanced_table}\n{placeholder.end_marker}"
        )

        if not validation_issues and confidence >= self.CONFIDENCE_THRESHOLD:
            # High confidence, valid structure - auto correct
            auto_corrections.append(
                AutoCorrection(
                    id=str(uuid4()),
                    observation_id=observation.id,
                    search=placeholder.full_match,
                    replace=new_content,
                    justification=(
                        f"Enhanced table: {reasoning}. "
                        f"Validated after {iterations} iteration(s)."
                    ),
                    confidence=confidence,
                    agent="tables",
                    page_num=placeholder.page_num,
                )
            )
            enhanced_content[placeholder.id] = enhanced_table
        else:
            # Low confidence or validation issues - needs review
            context = self._build_table_context(
                placeholder, enhanced_table, validation_issues
            )

            # Determine review reason
            if validation_issues:
                reason = f"Validation issues: {', '.join(i.type for i in validation_issues)}"
            else:
                reason = f"Low confidence ({confidence:.2f})"

            review_items.append(
                ReviewItem(
                    id=str(uuid4()),
                    observation_id=observation.id,
                    agent="tables",
                    question=f"Verify table on page {placeholder.page_num}",
                    options=[
                        ReviewOption(
                            id="accept",
                            label="Accept enhanced table",
                            action="replace",
                            replacement_text=new_content,
                            is_recommended=True,
                        ),
                        ReviewOption(
                            id="keep_original",
                            label="Keep original table",
                            action="keep",
                            is_recommended=False,
                        ),
                        ReviewOption(
                            id="other",
                            label="Edit table manually",
                            action="other",
                            is_recommended=False,
                        ),
                    ],
                    search_text=placeholder.full_match,
                    context=context,
                    page_num=placeholder.page_num,
                    agent_recommendation=f"{reasoning}. Review reason: {reason}",
                    agent_confidence=confidence,
                )
            )

    def _build_table_context(
        self,
        placeholder: TablePlaceholder,
        enhanced: str,
        issues: list[TableValidationIssue],
    ) -> str:
        """Build context for review item.

        Args:
            placeholder: Table placeholder
            enhanced: Enhanced table markdown
            issues: Validation issues

        Returns:
            Context string for review
        """
        context = f"**Original table:**\n```\n{placeholder.content}\n```\n\n"
        context += f"**Enhanced table:**\n```\n{enhanced}\n```\n\n"

        if issues:
            context += "**Validation issues:**\n"
            for issue in issues:
                context += f"- {issue.type}: {issue.description}\n"

        return context

    def _build_summary(
        self,
        placeholders: list[TablePlaceholder],
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> str:
        """Build human-readable summary.

        Args:
            placeholders: All found placeholders
            auto_corrections: List of auto-corrections
            review_items: List of review items

        Returns:
            Summary string
        """
        total = len(placeholders)
        auto = len(auto_corrections)
        review = len(review_items)
        return f"Processed {total} tables. {auto} auto-corrected, {review} need review."

    def _calculate_confidence(
        self,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> float:
        """Calculate overall confidence.

        Args:
            auto_corrections: List of auto-corrections
            review_items: List of review items

        Returns:
            Average confidence score
        """
        if not auto_corrections and not review_items:
            return 1.0

        all_confidences = [c.confidence for c in auto_corrections]
        all_confidences.extend([r.agent_confidence for r in review_items])

        return sum(all_confidences) / len(all_confidences) if all_confidences else 0.5


__all__ = ["TablesAgent", "TablePlaceholder", "TableValidationIssue", "TableEnhanceOutput"]
