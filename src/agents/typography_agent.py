"""Typography agent for semantic typography analysis.

Analyzes typography-based semantics: bold emphasis, italic definitions,
color-coding, and font size changes that convey meaning.

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
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic_ai import Agent, RunContext

from src.agents.dependencies import AgentDependencies
from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent_with_debug
from src.agents.helpers import extract_page_markdown, get_page_features
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import TypographyAnalysisOutput, TypographyIssue
from src.config import settings
from src.services.pdf_converter import PageData
from src.services.reasoning_corpus_service import get_reasoning_corpus_service
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest

logger = logging.getLogger(__name__)

# Module-level state for lazy initialization
_agent: Agent[AgentDependencies, TypographyAnalysisOutput] | None = None
_prompts: dict[str, Any] | None = None
_MODEL_TIER = ModelTier.REASONING


def get_agent() -> Agent[AgentDependencies, TypographyAnalysisOutput]:
    """Get or create the typography agent (lazy initialization)."""
    global _agent, _prompts

    if _agent is None:
        _prompts = load_prompts("typography.yaml")
        _agent = create_agent(
            "typography.yaml",
            TypographyAnalysisOutput,
            model_tier=_MODEL_TIER,
            use_deps=True,
            max_retries=2,
        )
        _register_dynamic_instructions(_agent)
        logger.info(f"TypographyAgent initialized with model tier {_MODEL_TIER.value}")

    return _agent


def reset_agent() -> None:
    """Reset the agent singleton for testing."""
    global _agent, _prompts
    _agent = None
    _prompts = None


def _register_dynamic_instructions(
    agent: Agent[AgentDependencies, TypographyAnalysisOutput]
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

    # Get prompts and agent
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("typography.yaml")

    agent = get_agent()

    # Create base deps if not provided
    if deps is None:
        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            document_type=manifest.document_type,
        )

    for page in pages:
        # Get page features for context
        page_features = get_page_features(manifest, page.page_num)

        if not page_features:
            continue

        # Create page-specific deps with context
        page_deps = deps.clone_for_page(
            page.page_num,
            complexity_score=page_features.complexity_score,
            layout_type=page_features.layout_type,
        )

        # Extract markdown section for this page
        page_markdown = extract_page_markdown(markdown, page.page_num)

        # Build complexity factors string
        if page_features.complexity_factors:
            complexity_factors = ", ".join(page_features.complexity_factors)
        else:
            complexity_factors = "none"

        # Build user message
        user_message = _prompts["user_prompt_template"].format(
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
            # Build image info for debug logging
            image_info = None
            if image_bytes and settings.debug_mode:
                image_info = {"size_bytes": len(image_bytes), "format": "png", "page_num": page.page_num}

            # Build message with image if available
            if image_bytes:
                result = await run_agent_with_debug(
                    agent=agent,
                    prompt=user_message,
                    job_id=job_id,
                    agent_name="typography_agent",
                    model_tier=_MODEL_TIER,
                    system_prompt=_prompts.get("system_prompt") if _prompts else None,
                    image_info=image_info,
                    deps=page_deps,
                    message_history=[
                        {  # type: ignore[list-item]
                            "role": "user",
                            "content": [
                                {"type": "text", "text": user_message},
                                {
                                    "type": "image",
                                    "data": image_bytes,
                                },
                            ],
                        }
                    ],
                )
            else:
                result = await run_agent_with_debug(
                    agent=agent,
                    prompt=user_message,
                    job_id=job_id,
                    agent_name="typography_agent",
                    model_tier=_MODEL_TIER,
                    system_prompt=_prompts.get("system_prompt") if _prompts else None,
                    deps=page_deps,
                )

            output = result.data  # type: ignore[attr-defined]
            usage = extract_usage(result, _MODEL_TIER)

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
            page_observations = _issues_to_observations(
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


def _issues_to_observations(
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
            severity=severity_value,
            route=cast(Literal["auto", "manual"], route),
            manual_reason=manual_reason,
        )
        observations.append(obs)

    return observations


# Wrapper class for AgentRouter compatibility
class TypographyAgent:
    """Wrapper class for AgentRouter protocol compatibility.

    This thin wrapper allows the module to be used with AgentRouter
    which expects classes with an analyze() method.
    """

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
        deps: AgentDependencies | None = None,
    ) -> tuple[list[Observation], LLMUsage]:
        return await analyze(pages, manifest, markdown, job_id, deps)


__all__ = ["TypographyAgent", "analyze", "get_agent", "reset_agent"]
