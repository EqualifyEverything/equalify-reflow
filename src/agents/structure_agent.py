"""StructureAgent for document structure analysis.

Specialized agent for analyzing document structure:
- Heading hierarchy validation (no skipped levels)
- Visual vs semantic heading alignment
- Reading order in multi-column layouts
- Section and landmark structure

Supports dynamic instructions via AgentDependencies for:
- Document context (title, type, total pages)
- Page-specific context (layout type)
- Retry guidance based on previous failures
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic_ai import Agent, RunContext

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import StructureAnalysisOutput, StructureIssue
from src.config import settings
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)


class StructureAgent(BaseDocumentAgent[StructureAnalysisOutput]):
    """Agent specialized in document structure analysis.

    Focuses on:
    - Heading hierarchy validation (no skipped levels)
    - Visual vs semantic heading alignment
    - Reading order in multi-column layouts
    - Section and landmark structure

    Uses Sonnet (REASONING tier) for structural reasoning.

    Note: Unlike other specialized agents, StructureAgent processes
    ALL pages to understand the complete document hierarchy.

    Supports dynamic instructions for:
    - Document context (title, type)
    - Page-specific context (layout type)
    - Retry guidance based on previous failures
    """

    def __init__(self) -> None:
        """Initialize StructureAgent with configuration."""
        config = AgentConfig(
            name="structure_agent",
            prompts_file=Path("structure.yaml"),
            output_type=StructureAnalysisOutput,
            correction_types=["heading_hierarchy", "reading_order", "landmarks"],
            max_retries=2,
            temperature=0.3,
            model_tier=ModelTier.REASONING,
            use_deps=True,  # Enable dynamic instructions
        )
        super().__init__(config)
        self._instructions_registered = False

    def _get_agent(self) -> Agent[AgentDependencies, StructureAnalysisOutput]:  # type: ignore[override]
        """Get or create agent with dynamic instructions registered."""
        agent = super()._get_agent()

        if not self._instructions_registered:
            self._register_dynamic_instructions(agent)  # type: ignore[arg-type]
            self._instructions_registered = True

        return agent  # type: ignore[return-value]

    def _register_dynamic_instructions(
        self, agent: Agent[AgentDependencies, StructureAnalysisOutput]
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
                layout_type = page_features.get("layout_type", "single_column")
                return f"""
<current_page>
Page {page_num}
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

        logger.debug("StructureAgent: Dynamic instructions registered")

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze document structure across all pages.

        The StructureAgent processes all pages together to understand
        the complete document hierarchy and reading order.

        Args:
            pages: All pages to analyze
            manifest: Document manifest with heading tree
            markdown: Current markdown content
            job_id: Job identifier
            deps: Optional AgentDependencies for dynamic instructions.
                  If not provided, creates default deps from manifest.

        Returns:
            Tuple of (observations for structural issues, combined usage metrics)
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

        # Parse heading tree for context
        heading_tree_summary = self._summarize_heading_tree(manifest.heading_tree_json)

        # Process pages - for structure, we analyze each page but with full context
        for page in pages:
            # Get layout type from page features
            layout_type = "single_column"
            for pf in manifest.page_features:
                if pf.page_num == page.page_num:
                    layout_type = pf.layout_type
                    break

            # Create page-specific deps with context
            page_deps = deps.clone_for_page(
                page.page_num,
                layout_type=layout_type,
            )

            # Extract markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build user message
            user_message = self.prompts["user_prompt_template"].format(
                page_num=page.page_num,
                document_title=manifest.document_title,
                layout_type=layout_type,
                heading_tree_summary=heading_tree_summary,
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
                    f"StructureAgent page {page.page_num}: "
                    f"Found {len(output.issues)} issues, "
                    f"hierarchy_valid={output.heading_hierarchy_valid}, "
                    f"order_valid={output.reading_order_valid}, "
                    f"cost: ${usage.estimated_cost_cents/100:.4f}"
                )

                # Log reasoning corpus for each issue
                corpus_service = get_reasoning_corpus_service()
                for issue in output.issues:
                    corpus = issue.extract_reasoning_corpus()
                    await corpus_service.log_corpus_batch(
                        job_id=job_id,
                        agent_name="structure",
                        corpus=corpus,
                    )

                # Convert issues to observations
                page_observations = self._issues_to_observations(
                    output.issues,
                    page.page_num,
                    job_id,
                )
                observations.extend(page_observations)

            except Exception as e:
                logger.error(
                    f"StructureAgent failed on page {page.page_num}: {e}",
                    exc_info=True,
                )
                # Continue with other pages

        return observations, combined_usage

    def _summarize_heading_tree(self, heading_tree_json: str) -> str:
        """Create a readable summary of the heading tree.

        Args:
            heading_tree_json: JSON string of HeadingTree

        Returns:
            Human-readable summary of document structure
        """
        try:
            tree = json.loads(heading_tree_json)
            lines = []

            # Add document title
            if "document_title" in tree:
                lines.append(f"Document: {tree['document_title']}")

            # Add sections
            sections = tree.get("sections", [])
            for section in sections[:20]:  # Limit to first 20 for context
                level = section.get("level", 1)
                title = section.get("title", "Untitled")
                page = section.get("page", "?")
                indent = "  " * (level - 1)
                lines.append(f"{indent}H{level}: {title} (p.{page})")

            if len(sections) > 20:
                lines.append(f"  ... and {len(sections) - 20} more sections")

            return "\n".join(lines) if lines else "No heading structure available"

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Could not parse heading tree: {e}")
            return "Heading structure unavailable"

    def _extract_page_markdown(self, markdown: str, page_num: int) -> str:
        """Extract approximate markdown section for a page."""
        lines = markdown.split("\n")
        if not lines:
            return ""

        chunk_size = max(len(lines) // 10, 30)
        start = (page_num - 1) * chunk_size
        end = min(page_num * chunk_size, len(lines))

        return "\n".join(lines[start:end])

    def _issues_to_observations(
        self,
        issues: list[StructureIssue],
        page_num: int,
        job_id: str,
    ) -> list[Observation]:
        """Convert StructureIssue objects to Observations.

        Args:
            issues: List of structure issues from agent
            page_num: Page number (for location)
            job_id: Job identifier

        Returns:
            List of Observation objects
        """
        observations: list[Observation] = []

        for issue in issues:
            # Access .value for Reasoned[T] fields
            severity_value = issue.severity.value
            issue_type_value = issue.issue_type.value

            # Map severity string to valid values
            severity = severity_value
            if severity not in ["critical", "major", "minor"]:
                severity = "major"

            # Determine routing
            route = "auto" if issue.confidence >= settings.min_confidence_for_auto_approval else "manual"
            manual_reason = None
            if route == "manual":
                manual_reason = "Low confidence in structural analysis"

            # Build location based on issue type
            location_type = "region"
            if issue_type_value in ["heading_skip", "heading_mismatch"]:
                location_type = "element"

            obs = Observation(
                id=str(uuid4()),
                job_id=job_id,
                agent="structure",
                source="agent",
                visual_description=issue.visual_evidence,
                markup_description=issue.markup_state,
                location=ObservationLocation(
                    location_type=cast(Literal["element", "range", "region"], location_type),
                    value=issue.location_description,
                    page_num=page_num,
                ),
                confidence=issue.confidence,
                severity=severity,
                route=cast(Literal["auto", "manual"], route),
                manual_reason=manual_reason,
            )
            observations.append(obs)

        return observations

    async def process(self, input_data: Any) -> StructureAnalysisOutput:
        """Process method required by BaseDocumentAgent.

        For StructureAgent, use analyze() method instead which provides
        the proper interface for specialized agents.
        """
        raise NotImplementedError(
            "StructureAgent uses analyze() method, not process()"
        )


__all__ = ["StructureAgent"]
