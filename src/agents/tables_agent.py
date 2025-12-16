"""TablesAgent for table structure analysis.

Specialized agent for analyzing tables in PDF documents:
- Verifying table headers are properly marked
- Detecting merged cell structures that may be lost
- Comparing visual table data to markdown accuracy
- Identifying tables that need restructuring

Supports dynamic instructions via AgentDependencies for:
- Document context (title, type, total pages)
- Page-specific context (expected tables, layout)
- Retry guidance based on previous failures
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic_ai import Agent, RunContext

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import TableAnalysis, TablesAnalysisOutput
from src.config import settings
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
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

    Supports dynamic instructions for:
    - Document context (title, type)
    - Page-specific context (expected tables, layout)
    - Retry guidance based on previous failures
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
            use_deps=True,  # Enable dynamic instructions
        )
        super().__init__(config)
        self._instructions_registered = False

    def _get_agent(self) -> Agent[AgentDependencies, TablesAnalysisOutput]:  # type: ignore[override]
        """Get or create agent with dynamic instructions registered."""
        agent = super()._get_agent()

        if not self._instructions_registered:
            self._register_dynamic_instructions(agent)  # type: ignore[arg-type]
            self._instructions_registered = True

        return agent  # type: ignore[return-value]

    def _register_dynamic_instructions(
        self, agent: Agent[AgentDependencies, TablesAnalysisOutput]
    ) -> None:
        """Register dynamic instruction generators on the agent."""

        @agent.instructions
        def document_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-level context."""
            if ctx.deps.manifest:
                return f"""
<document_context>
Title: {ctx.deps.manifest.document_title}
Type: {ctx.deps.manifest.document_type}
Total pages: {ctx.deps.manifest.total_pages}
</document_context>"""
            return ""

        @agent.instructions
        def page_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide page-specific context from manifest."""
            page_features = ctx.deps.custom_context.get("current_page_features")
            if page_features:
                page_num = page_features.get("page_num", "?")
                table_count = page_features.get("table_count", 0)
                layout_type = page_features.get("layout_type", "single_column")
                return f"""
<current_page>
Page {page_num}: {table_count} tables expected
Layout: {layout_type}
</current_page>"""
            return ""

        @agent.instructions
        def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide guidance for retry attempts."""
            if not ctx.deps.is_retry:
                return ""

            failures = "\n".join(f"- {f}" for f in ctx.deps.previous_failures[-3:])
            return f"""
<retry_attempt number="{ctx.deps.attempt_number}">
Previous attempts encountered issues:
{failures}

Adjust your approach to avoid these issues.
</retry_attempt>"""

        logger.debug("TablesAgent: Dynamic instructions registered")

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze tables on provided pages.

        Args:
            pages: Pages with tables to analyze
            manifest: Document manifest with page features
            markdown: Current markdown content
            job_id: Job identifier
            deps: Optional AgentDependencies for dynamic instructions.
                  If not provided, creates default deps from manifest.

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

        # Create base deps if not provided
        if deps is None:
            deps = AgentDependencies(
                job_id=job_id,
                manifest=manifest,
                document_type=manifest.document_type,
            )

        for page in pages:
            # Get page features for this page
            page_features = self._get_page_features(manifest, page.page_num)

            if not page_features or not page_features.has_tables:
                continue

            # Create page-specific deps with context
            page_deps = deps.clone_for_page(
                page.page_num,
                table_count=page_features.table_count,
                layout_type=page_features.layout_type,
            )

            # Extract approximate markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build user message
            user_message = self.prompts["user_prompt_template"].format(
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
                # Run analysis with deps for dynamic instructions
                output, usage = await self._run_with_deps(
                    user_message, page_deps, image_bytes
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

                # Log reasoning corpus for each analysis
                corpus_service = get_reasoning_corpus_service()
                for analysis in output.analyses:
                    corpus = analysis.extract_reasoning_corpus()
                    await corpus_service.log_corpus_batch(
                        job_id=job_id,
                        agent_name="tables",
                        corpus=corpus,
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
            # Access .value for Reasoned[T] fields
            complexity_value = analysis.complexity.value
            action_value = analysis.recommended_action.value

            # Skip if no action needed
            if action_value == "none":
                continue

            # Determine severity based on complexity and data accuracy
            severity: str
            if analysis.data_accuracy in ["missing_data", "structural_loss"]:
                severity = "critical"
            elif complexity_value in ["merged_cells", "nested", "irregular"]:
                severity = "major"
            elif not analysis.has_headers:
                severity = "major"
            else:
                severity = "minor"

            # Determine routing - complex tables usually need manual review
            route: str
            manual_reason: str | None = None

            if analysis.confidence < settings.min_confidence_for_auto_approval:
                route = "manual"
                manual_reason = "Low confidence in table analysis"
            elif complexity_value in ["merged_cells", "nested", "irregular"]:
                route = "manual"
                manual_reason = (
                    f"Complex table structure ({complexity_value}) "
                    "requires human judgment on best representation"
                )
            else:
                route = "auto"

            # Build visual description including issues
            issues_str = ", ".join(analysis.markdown_issues) if analysis.markdown_issues else "none identified"
            visual_desc = f"{analysis.visual_description}. Issues: {issues_str}"

            # Build markup description including action reasoning
            markup_desc = (
                f"Table {analysis.table_index}: "
                f"headers={analysis.header_structure}, "
                f"complexity={complexity_value}, "
                f"accuracy={analysis.data_accuracy}, "
                f"action={action_value}"
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
