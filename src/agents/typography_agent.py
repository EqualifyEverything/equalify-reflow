"""TypographyAgent for semantic typography analysis (PRD-014, Issue #23).

Specialized agent for analyzing typography-based semantics:
- Bold text conveying emphasis (should use <strong>)
- Italic text indicating terms/definitions
- Color-coding that conveys meaning
- Font size changes suggesting structure
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.agents.model_tiers import ModelTier
from src.agents.specialized_models import TypographyAnalysisOutput, TypographyIssue
from src.services.pdf_converter import PageData
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
        )
        super().__init__(config)

    def _default_prompts(self) -> dict[str, Any]:
        """Provide fallback prompts if YAML file not found."""
        return {
            "system_prompt": """You are a typography semantics expert analyzing PDF documents.
Identify visual styling that conveys meaning and should be preserved in markup.

SEMANTIC PATTERNS:
- Bold often indicates emphasis or importance (**strong**)
- Italic may indicate terms, titles, foreign words (*em*)
- Color-coding may indicate categories or status
- Size changes may suggest heading hierarchy

ISSUE TYPES:
- emphasis_unmarked: Bold/italic conveying emphasis not in markdown
- definition_unmarked: Terms being defined not marked
- semantic_color: Color conveys meaning without text alternative
- visual_heading: Font size/weight suggests heading not captured

AVOID FALSE POSITIVES:
- Decorative styling vs semantic styling
- Brand fonts, design elements
- Consistent styling that doesn't convey meaning""",
            "user_prompt_template": """Analyze typography on page {page_num}.
Document: {document_title}
Complexity score: {complexity_score}
Factors: {complexity_factors}

Current markdown:
```
{page_markdown}
```

Identify semantic typography not captured in markup.
Focus on HIGH-CONFIDENCE findings only.""",
        }

    async def analyze(
        self,
        pages: list[PageData],
        manifest: DocumentManifest,
        markdown: str,
        job_id: str,
    ) -> tuple[list[Observation], LLMUsage]:
        """Analyze typography for semantic meaning.

        The TypographyAgent focuses on pages with higher complexity
        where typography is more likely to carry semantic meaning.

        Args:
            pages: Pages to analyze (filtered to complexity > 0.5)
            manifest: Document manifest with page features
            markdown: Current markdown content
            job_id: Job identifier

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

        for page in pages:
            # Get page features for context
            page_features = self._get_page_features(manifest, page.page_num)

            if not page_features:
                continue

            # Extract markdown section for this page
            page_markdown = self._extract_page_markdown(markdown, page.page_num)

            # Build complexity factors string
            if page_features.complexity_factors:
                complexity_factors = ", ".join(page_features.complexity_factors)
            else:
                complexity_factors = "none"

            # Build user message
            user_message = self.prompts.get(
                "user_prompt_template",
                self._default_prompts()["user_prompt_template"]
            ).format(
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
                    f"TypographyAgent page {page.page_num}: "
                    f"Found {len(output.issues)} issues, "
                    f"cost: ${usage.estimated_cost_cents/100:.4f}"
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
            # Typography issues are typically minor unless semantic
            severity: str
            if issue.issue_type == "semantic_color":
                severity = "major"  # Color conveying meaning without alternative
            elif issue.issue_type == "visual_heading":
                severity = "major"  # Missing heading is structural
            else:
                severity = "minor"  # Emphasis/definition are nice-to-have

            # Determine routing
            route = "auto" if issue.confidence >= 0.7 else "manual"
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
                severity=cast(Literal["critical", "major", "minor"], severity),
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
