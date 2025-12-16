"""TablesAgent for table structure analysis (PRD-014, Issue #24).

Specialized agent for analyzing tables in PDF documents:
- Verifying table headers are properly marked
- Detecting merged cell structures that may be lost
- Comparing visual table data to markdown accuracy
- Identifying tables that need restructuring
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import TableAnalysis, TablesAnalysisOutput
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

logger = logging.getLogger(__name__)


class TablesAgent(BaseDocumentAgent[TablesAnalysisOutput]):
    """Agent specialized in table accessibility analysis.

    Focuses on:
    - Verifying table headers are properly marked
    - Detecting merged cell structures that may be lost
    - Comparing visual table data to markdown accuracy
    - Identifying tables that need restructuring

    Uses Sonnet (REASONING tier) for accurate structure analysis.
    """

    def __init__(self) -> None:
        """Initialize TablesAgent with configuration."""
        config = AgentConfig(
            name="tables_agent",
            prompts_file=Path("tables.yaml"),
            output_type=TablesAnalysisOutput,
            correction_types=["table_headers", "table_structure", "table_data"],
            max_retries=2,
            temperature=0.3,
            model_tier=ModelTier.REASONING,
        )
        super().__init__(config)

    def _default_prompts(self) -> dict[str, Any]:
        """Provide fallback prompts if YAML file not found."""
        return {
            "system_prompt": """You are a table accessibility expert analyzing PDF documents.
Evaluate table structure and data accuracy for accessibility compliance.

HEADER STRUCTURE:
- single_row: Simple header row at top
- multi_row: Multiple header rows (complex)
- column_headers: Headers in first column
- none: No identifiable headers

COMPLEXITY:
- simple: Regular grid, single header row
- merged_cells: Has spanning cells
- nested: Tables within tables
- irregular: Varying column counts

DATA ACCURACY:
- accurate: Markdown matches visual exactly
- partial: Some data missing/misaligned
- missing_data: Significant data loss
- structural_loss: Structure fundamentally changed

ACTIONS:
- add_headers: Needs header identification
- restructure: Needs significant changes
- verify_data: Needs human verification
- add_caption: Would benefit from caption
- none: Structure is appropriate""",
            "user_prompt_template": """Analyze tables on page {page_num}.
Document: {document_title}
Expected tables: {expected_table_count}

Current markdown:
```
{page_markdown}
```

Analyze each table for structure and accuracy.""",
        }

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze tables on provided pages.

        Args:
            pages: Pages with tables to analyze
            manifest: Document manifest with page features
            markdown: Current markdown content
            job_id: Job identifier

        Returns:
            Tuple of (observations for table structure issues, combined usage metrics)
        """
        observations: list[Observation] = []
        combined_usage = LLMUsage(
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
            estimated_cost_cents=0.0,
        )

        for page in pages:
            # Get page features for this page
            page_features = self._get_page_features(manifest, page.page_num)

            if not page_features or not page_features.has_tables:
                continue

            # Extract approximate markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build user message
            user_message = self.prompts.get(
                "user_prompt_template",
                self._default_prompts()["user_prompt_template"]
            ).format(
                page_num=page.page_num,
                document_title=manifest.document_title,
                expected_table_count=page_features.table_count,
                page_markdown=page_markdown,
            )

            # Decode image for multimodal input
            image_bytes = None
            if page.image_base64:
                image_bytes = base64.b64decode(page.image_base64)

            try:
                # Run analysis
                output, usage = await self._run_agent(
                    user_message, image_bytes, job_id=job_id, page_num=page.page_num
                )

                # Accumulate usage
                combined_usage.input_tokens += usage.input_tokens
                combined_usage.output_tokens += usage.output_tokens
                combined_usage.total_tokens += usage.total_tokens
                combined_usage.estimated_cost_cents += usage.estimated_cost_cents

                logger.debug(
                    f"TablesAgent page {page.page_num}: "
                    f"Found {output.tables_found} tables, "
                    f"{len(output.analyses)} analyses, "
                    f"cost: ${usage.estimated_cost_cents/100:.4f}"
                )

                # Convert analyses to observations
                page_observations = self._analyses_to_observations(
                    output.analyses,
                    page.page_num,
                    job_id,
                )
                observations.extend(page_observations)

            except Exception as e:
                logger.error(
                    f"TablesAgent failed on page {page.page_num}: {e}",
                    exc_info=True,
                )
                # Continue with other pages

        return observations, combined_usage

    def _get_page_features(
        self,
        manifest: DocumentManifest,
        page_num: int,
    ) -> PageFeatures | None:
        """Get PageFeatures for a specific page number."""
        for pf in manifest.page_features:
            if pf.page_num == page_num:
                return pf
        return None

    def _extract_page_markdown(self, markdown: str, page_num: int) -> str:
        """Extract approximate markdown section for a page."""
        lines = markdown.split("\n")
        if not lines:
            return ""

        chunk_size = max(len(lines) // 10, 30)
        start = (page_num - 1) * chunk_size
        end = min(page_num * chunk_size, len(lines))

        return "\n".join(lines[start:end])

    def _analyses_to_observations(
        self,
        analyses: list[TableAnalysis],
        page_num: int,
        job_id: str,
    ) -> list[Observation]:
        """Convert TableAnalysis objects to Observations.

        Args:
            analyses: List of table analyses from agent
            page_num: Page number
            job_id: Job identifier

        Returns:
            List of Observation objects
        """
        observations: list[Observation] = []

        for analysis in analyses:
            # Skip if no action needed
            if analysis.recommended_action == "none":
                continue

            # Determine severity based on complexity and data accuracy
            severity: str
            if analysis.data_accuracy in ["missing_data", "structural_loss"]:
                severity = "critical"
            elif analysis.complexity in ["merged_cells", "nested", "irregular"]:
                severity = "major"
            elif not analysis.has_headers:
                severity = "major"
            else:
                severity = "minor"

            # Determine routing - complex tables usually need manual review
            route: str
            manual_reason: str | None = None

            if analysis.confidence < 0.7:
                route = "manual"
                manual_reason = "Low confidence in table analysis"
            elif analysis.complexity in ["merged_cells", "nested", "irregular"]:
                route = "manual"
                manual_reason = (
                    f"Complex table structure ({analysis.complexity}) "
                    "requires human judgment on best representation"
                )
            else:
                route = "auto"

            # Build visual description including issues
            issues_str = ", ".join(analysis.markdown_issues) if analysis.markdown_issues else "none identified"
            visual_desc = f"{analysis.visual_description}. Issues: {issues_str}"

            # Build markup description
            markup_desc = (
                f"Table {analysis.table_index}: "
                f"headers={analysis.header_structure}, "
                f"complexity={analysis.complexity}, "
                f"accuracy={analysis.data_accuracy}"
            )

            obs = Observation(
                id=str(uuid4()),
                job_id=job_id,
                agent="tables",
                source="agent",
                visual_description=visual_desc,
                markup_description=markup_desc,
                location=ObservationLocation(
                    location_type="region",
                    value=f"Table {analysis.table_index} on page {page_num}",
                    page_num=page_num,
                ),
                confidence=analysis.confidence,
                severity=cast(Literal["critical", "major", "minor"], severity),
                route=cast(Literal["auto", "manual"], route),
                manual_reason=manual_reason,
            )
            observations.append(obs)

        return observations

    async def process(self, input_data: Any) -> TablesAnalysisOutput:
        """Process method required by BaseDocumentAgent.

        For TablesAgent, use analyze() method instead which provides
        the proper interface for specialized agents.
        """
        raise NotImplementedError(
            "TablesAgent uses analyze() method, not process()"
        )


__all__ = ["TablesAgent"]
