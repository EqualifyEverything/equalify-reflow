"""TypographyAgent for semantic typography analysis.

Specialized agent for analyzing typography-based semantics:
- Bold text conveying emphasis (should use <strong>)
- Italic text indicating terms/definitions
- Color-coding that conveys meaning
- Font size changes suggesting structure

Supports dynamic instructions via AgentDependencies for:
- Document context (title, type, total pages)
- Page-specific context (complexity score, layout)
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
from src.agents.specialized_models import TypographyAnalysisOutput, TypographyIssue
from src.config import settings
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures

logger = logging.getLogger(__name__)


class TypographyAgent(BaseDocumentAgent[TypographyAnalysisOutput]):
    """Agent specialized in semantic typography analysis.

    Focuses on:
    - Bold text conveying emphasis (should use **strong**)
    - Italic text indicating terms/definitions
    - Color-coding that conveys meaning
    - Font size changes suggesting structure

    Uses Sonnet (REASONING tier) for semantic understanding.

    Note: TypographyAgent only processes pages with complexity_score > 0.5
    to focus on content where typography is likely to carry meaning.

    Supports dynamic instructions for:
    - Document context (title, type)
    - Page-specific context (complexity score, layout)
    - Retry guidance based on previous failures
    """

    def __init__(self) -> None:
        """Initialize TypographyAgent with configuration."""
        config = AgentConfig(
            name="typography_agent",
            prompts_file=Path("typography.yaml"),
            output_type=TypographyAnalysisOutput,
            correction_types=["emphasis", "definition", "semantic_color"],
            max_retries=2,
            temperature=0.3,
            model_tier=ModelTier.REASONING,
            use_deps=True,  # Enable dynamic instructions
        )
        super().__init__(config)
        self._instructions_registered = False

    def _get_agent(self) -> Agent[AgentDependencies, TypographyAnalysisOutput]:  # type: ignore[override]
        """Get or create agent with dynamic instructions registered."""
        agent = super()._get_agent()

        if not self._instructions_registered:
            self._register_dynamic_instructions(agent)  # type: ignore[arg-type]
            self._instructions_registered = True

        return agent  # type: ignore[return-value]

    def _register_dynamic_instructions(
        self, agent: Agent[AgentDependencies, TypographyAnalysisOutput]
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
        def document_type_guidance(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-type-specific typography guidance."""
            doc_type = ctx.deps.document_type
            if doc_type == "research_paper":
                return """
<document_type_guidance>
Research papers commonly use:
- Italics for Latin terms (p < 0.05, in vitro, et al.) - DECORATIVE (discipline convention)
- Bold for statistical significance markers - potentially SEMANTIC if not standard
- Italics for journal/book titles in citations - DECORATIVE (citation convention)
- Bold section headers - DECORATIVE (structural, should be headings)

Be conservative: most academic styling follows discipline conventions, not semantic emphasis.
Only flag styling that conveys meaning BEYOND standard academic formatting.
</document_type_guidance>"""
            elif doc_type == "syllabus":
                return """
<document_type_guidance>
Syllabi commonly use:
- Bold for important dates/deadlines - SEMANTIC (flag if not in markdown)
- Color-coding for assignment types (required vs optional) - SEMANTIC (flag)
- Bold for policy headers like "Late Work:" - potentially SEMANTIC
- Italics for course/textbook titles - DECORATIVE (title convention)

Flag: Bold dates, color-coded categories without text labels, emphasized warnings.
Skip: Standard title formatting, consistent header styling.
</document_type_guidance>"""
            elif doc_type == "exam":
                return """
<document_type_guidance>
Exams commonly use:
- Bold for point values - DECORATIVE (structural convention)
- Bold for question numbers - DECORATIVE (structural)
- Italics for instructions - potentially SEMANTIC if emphasis
- Color for correct answers (in answer keys) - SEMANTIC (flag)

Flag: Color-only answer indicators, emphasized warnings about time limits.
Skip: Standard question formatting, point value styling.
</document_type_guidance>"""
            elif doc_type == "lecture_notes":
                return """
<document_type_guidance>
Lecture notes commonly use:
- Bold for key terms being introduced - SEMANTIC (flag if first occurrence)
- Color for emphasis or categorization - potentially SEMANTIC
- Italics for examples or asides - DECORATIVE typically
- Bold headers - DECORATIVE (should be headings)

Flag: Bold key terms not captured, color-coded categories.
Skip: Slide formatting conventions, consistent styling patterns.
</document_type_guidance>"""
            return ""

        @agent.instructions
        def page_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide page-specific context from manifest."""
            page_features = ctx.deps.custom_context.get("current_page_features")
            if page_features:
                page_num = page_features.get("page_num", "?")
                complexity_score = page_features.get("complexity_score", 0)
                layout_type = page_features.get("layout_type", "single_column")
                return f"""
<current_page>
Page {page_num}
Complexity: {complexity_score:.2f}
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

        logger.debug("TypographyAgent: Dynamic instructions registered")

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze typography for semantic meaning.

        The TypographyAgent focuses on pages with higher complexity
        where typography is more likely to carry semantic meaning.

        Args:
            pages: Pages to analyze (filtered to complexity > 0.5)
            manifest: Document manifest with page features
            markdown: Current markdown content
            job_id: Job identifier
            deps: Optional AgentDependencies for dynamic instructions.
                  If not provided, creates default deps from manifest.

        Returns:
            Tuple of (observations for typography issues, combined usage metrics)
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
            # Get page features for context
            page_features = self._get_page_features(manifest, page.page_num)

            if not page_features:
                continue

            # Create page-specific deps with context
            page_deps = deps.clone_for_page(
                page.page_num,
                complexity_score=page_features.complexity_score,
                layout_type=page_features.layout_type,
            )

            # Extract markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build complexity factors string
            if page_features.complexity_factors:
                complexity_factors = ", ".join(page_features.complexity_factors)
            else:
                complexity_factors = "none"

            # Build user message
            user_message = self.prompts["user_prompt_template"].format(
                page_num=page.page_num,
                document_title=manifest.document_title,
                complexity_score=page_features.complexity_score,
                complexity_factors=complexity_factors,
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
                    f"TypographyAgent page {page.page_num}: "
                    f"Found {len(output.issues)} issues, "
                    f"cost: ${usage.estimated_cost_cents/100:.4f}"
                )

                # Log reasoning corpus for each issue
                corpus_service = get_reasoning_corpus_service()
                for issue in output.issues:
                    corpus = issue.extract_reasoning_corpus()
                    await corpus_service.log_corpus_batch(
                        job_id=job_id,
                        agent_name="typography",
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
                    f"TypographyAgent failed on page {page.page_num}: {e}",
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

    def _issues_to_observations(
        self,
        issues: list[TypographyIssue],
        page_num: int,
        job_id: str,
    ) -> list[Observation]:
        """Convert TypographyIssue objects to Observations.

        Args:
            issues: List of typography issues from agent
            page_num: Page number
            job_id: Job identifier

        Returns:
            List of Observation objects
        """
        observations: list[Observation] = []

        for issue in issues:
            # Access .value for Reasoned[T] fields
            severity_value = issue.severity.value

            # Determine routing based on hybrid confidence
            route = "auto" if issue.confidence >= settings.min_confidence_for_auto_approval else "manual"
            manual_reason = None
            if route == "manual":
                manual_reason = "Low confidence in typography semantic analysis"

            obs = Observation(
                id=str(uuid4()),
                job_id=job_id,
                agent="typography",
                source="agent",
                visual_description=issue.visual_description,
                markup_description=issue.markup_state,
                location=ObservationLocation(
                    location_type="region",
                    value=f"Typography on page {page_num}: {issue.semantic_meaning}",
                    page_num=page_num,
                ),
                confidence=issue.confidence,
                severity=cast(Literal["critical", "major", "minor"], severity_value),
                route=cast(Literal["auto", "manual"], route),
                manual_reason=manual_reason,
            )
            observations.append(obs)

        return observations

    async def process(self, input_data: Any) -> TypographyAnalysisOutput:
        """Process method required by BaseDocumentAgent.

        For TypographyAgent, use analyze() method instead which provides
        the proper interface for specialized agents.
        """
        raise NotImplementedError(
            "TypographyAgent uses analyze() method, not process()"
        )


__all__ = ["TypographyAgent"]
