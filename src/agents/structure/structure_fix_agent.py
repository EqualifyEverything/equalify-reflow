"""Structure fix agent for semantic structural corrections.

This agent handles structural issues that cannot be auto-fixed by deterministic
tools (like mdformat). It makes decisions with full document context:

1. OCR error decisions - decides in context, NEVER auto-corrects
2. Heading hierarchy fixes - adjusts levels to maintain proper hierarchy
3. Complex structural issues - fixes issues requiring semantic understanding

Called by StructureLoop service in Phase 3a: Refine.
Uses Haiku (EFFICIENT tier) for cost efficiency.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent

from src.agents.factory import create_agent, extract_usage, load_prompts, run_agent
from src.agents.model_tiers import ModelTier
from src.shared.models.observation import Observation, ObservationLocation

if TYPE_CHECKING:
    from src.services.structure_loop import HeadingIssue, LintIssue
    from src.shared.models.remediation import DocumentManifest
    from src.utils.ocr_checker import OCRSuggestion

logger = logging.getLogger(__name__)

# Module-level state
_agent: Agent[None, StructureFixOutput] | None = None
_prompts: dict | None = None
_MODEL_TIER = ModelTier.EFFICIENT  # Haiku for cost efficiency


# =============================================================================
# Output Models
# =============================================================================


class OCRDecision(BaseModel):
    """Decision on a potential OCR error.

    Attributes:
        word: The word flagged as potential OCR error
        decision: What to do (fix, keep, uncertain)
        replacement: The replacement text (only if decision is "fix")
        reasoning: Why this decision was made (glass box)

    Example:
        >>> decision = OCRDecision(
        ...     word="Exxon",
        ...     decision="fix",
        ...     replacement="Enzo",
        ...     reasoning="Document discusses the Enzo project, not the oil company"
        ... )
    """

    word: str = Field(..., description="The word flagged as potential OCR error")
    decision: Literal["fix", "keep", "uncertain"] = Field(
        ...,
        description="What to do: fix, keep, or uncertain"
    )
    replacement: str | None = Field(
        default=None,
        description="Replacement text (only if decision is 'fix')"
    )
    reasoning: str = Field(
        ...,
        description="Why this decision was made"
    )


class StructureFixOutput(BaseModel):
    """Output from structure fix agent.

    Attributes:
        corrected_markdown: The corrected markdown text
        correction_summary: Human-readable summary of corrections made
        ocr_decisions: Decisions on each OCR suggestion
        heading_fixes: Description of heading hierarchy fixes
        other_fixes: Description of other structural fixes

    Example:
        >>> output = StructureFixOutput(
        ...     corrected_markdown="# Title\\n\\n## Section 1\\n...",
        ...     correction_summary="Fixed 2 OCR errors, 1 heading hierarchy issue",
        ...     ocr_decisions=[decision1, decision2],
        ...     heading_fixes="Adjusted H3 to H2 at line 15",
        ...     other_fixes="None"
        ... )
    """

    corrected_markdown: str = Field(
        ...,
        description="The corrected markdown text"
    )
    correction_summary: str = Field(
        ...,
        description="Human-readable summary of corrections"
    )
    ocr_decisions: list[OCRDecision] = Field(
        default_factory=list,
        description="Decisions on each OCR suggestion"
    )
    heading_fixes: str = Field(
        default="",
        description="Description of heading hierarchy fixes"
    )
    other_fixes: str = Field(
        default="",
        description="Description of other structural fixes"
    )


class StructureFixResult(BaseModel):
    """Full result from structure fix operation.

    Extends StructureFixOutput with observations and cost tracking.

    Attributes:
        corrected_markdown: The corrected markdown text
        correction_summary: Summary of corrections
        ocr_decisions: OCR decisions made
        observations: Observations generated for review
        cost_cents: LLM cost for this operation
    """

    corrected_markdown: str
    correction_summary: str
    ocr_decisions: list[OCRDecision] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    cost_cents: float = 0.0


# =============================================================================
# Agent Functions
# =============================================================================


def get_prompts() -> dict:
    """Load structure fix agent prompts from YAML."""
    global _prompts
    if _prompts is None:
        _prompts = load_prompts("structure_fix.yaml")
    return _prompts


def get_agent() -> Agent[None, StructureFixOutput]:
    """Get or create the structure fix agent."""
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="structure_fix.yaml",
            output_type=StructureFixOutput,
            model_tier=_MODEL_TIER,
            use_deps=False,
            max_retries=2,
        )
    return _agent


async def fix_structural_issues(
    markdown: str,
    lint_issues: list[LintIssue],
    heading_issues: list[HeadingIssue],
    ocr_suggestions: list[OCRSuggestion],
    manifest: DocumentManifest,
    job_id: str,
) -> StructureFixResult:
    """Fix structural issues with full document context.

    This function:
    1. Builds context from document manifest (key terms, document type)
    2. Formats issues for the LLM prompt
    3. Runs the agent to fix issues
    4. Converts output to StructureFixResult with observations

    Args:
        markdown: Current markdown text
        lint_issues: Lint issues to fix (semantic only, not formatting)
        heading_issues: Heading hierarchy issues
        ocr_suggestions: OCR suggestions for review (NEVER auto-corrected)
        manifest: Document manifest with context
        job_id: Job ID for correlation

    Returns:
        StructureFixResult with corrected markdown and metadata
    """
    agent = get_agent()
    prompts = get_prompts()

    # Build document context
    document_context = _build_document_context(manifest)
    key_entities = _get_key_entities(manifest)
    domain_terms = _get_domain_terms(manifest)

    # Format issues for prompt
    lint_str = _format_lint_issues(lint_issues)
    heading_str = _format_heading_issues(heading_issues)
    ocr_str = _format_ocr_suggestions(ocr_suggestions)

    # Build user prompt
    user_prompt = prompts["user_prompt"].format(
        document_context=document_context,
        key_entities=", ".join(key_entities) if key_entities else "None provided",
        domain_terms=", ".join(domain_terms) if domain_terms else "None provided",
        lint_issues=lint_str or "None",
        heading_issues=heading_str or "None",
        ocr_suggestions=ocr_str or "None",
        markdown=markdown,
    )

    logger.info(
        f"Job {job_id}: Running structure fix agent - "
        f"{len(lint_issues)} lint issues, "
        f"{len(heading_issues)} heading issues, "
        f"{len(ocr_suggestions)} OCR suggestions"
    )

    result = await run_agent(
        agent=agent,
        prompt=user_prompt,
        job_id=job_id,
        agent_name="structure_fix",
        model_tier=_MODEL_TIER,
        system_prompt=prompts["system_prompt"],
    )

    output: StructureFixOutput = result.output
    usage = extract_usage(result, _MODEL_TIER)

    # Generate observations from OCR decisions
    observations = _generate_ocr_observations(output.ocr_decisions, job_id)

    # Add observation for heading fixes if any
    if output.heading_fixes and output.heading_fixes.lower() != "none":
        observations.append(Observation(
            job_id=job_id,
            agent="structure",
            visual_description="Heading hierarchy issue detected",
            markup_description=output.heading_fixes,
            location=ObservationLocation(
                location_type="region",
                value="Heading hierarchy",
                page_num=1,
            ),
            confidence=0.9,
            severity="minor",
            category="heading",
            status="closed",
            resolution="fixed",
        ))

    logger.info(
        f"Job {job_id}: Structure fix complete - "
        f"summary: {output.correction_summary[:100]}..., "
        f"cost: ${usage.estimated_cost_cents/100:.4f}"
    )

    return StructureFixResult(
        corrected_markdown=output.corrected_markdown,
        correction_summary=output.correction_summary,
        ocr_decisions=output.ocr_decisions,
        observations=observations,
        cost_cents=usage.estimated_cost_cents,
    )


# =============================================================================
# Helper Functions
# =============================================================================


def _build_document_context(manifest: DocumentManifest) -> str:
    """Build document context string for the prompt.

    Args:
        manifest: Document manifest

    Returns:
        Formatted context string
    """
    parts = [
        f"Document Type: {manifest.document_type}",
        f"Title: {manifest.document_title}",
        f"Total Pages: {manifest.total_pages}",
    ]

    if manifest.summary:
        parts.append(f"Topic: {manifest.summary.topic_summary}")
        parts.append(f"Audience: {manifest.summary.audience_level}")

    return "\n".join(parts)


def _get_key_entities(manifest: DocumentManifest) -> list[str]:
    """Get key entities from manifest.

    Args:
        manifest: Document manifest

    Returns:
        List of key entities (names, projects, technical terms)
    """
    if manifest.summary and manifest.summary.key_entities:
        return manifest.summary.key_entities
    return []


def _get_domain_terms(manifest: DocumentManifest) -> list[str]:
    """Get domain terms from manifest.

    Args:
        manifest: Document manifest

    Returns:
        List of domain-specific terms
    """
    if manifest.summary and manifest.summary.domain_terms:
        return manifest.summary.domain_terms
    return []


def _format_lint_issues(issues: list[LintIssue]) -> str:
    """Format lint issues for the prompt.

    Args:
        issues: List of lint issues

    Returns:
        Formatted string describing issues
    """
    if not issues:
        return ""

    lines = []
    for issue in issues:
        lines.append(f"- Line {issue.line}: [{issue.rule}] {issue.message}")
    return "\n".join(lines)


def _format_heading_issues(issues: list[HeadingIssue]) -> str:
    """Format heading issues for the prompt.

    Args:
        issues: List of heading issues

    Returns:
        Formatted string describing issues
    """
    if not issues:
        return ""

    lines = []
    for issue in issues:
        locations = ", ".join(str(loc) for loc in issue.locations)
        lines.append(f"- {issue.type}: {issue.description} (lines: {locations})")
    return "\n".join(lines)


def _format_ocr_suggestions(suggestions: list[OCRSuggestion]) -> str:
    """Format OCR suggestions for the prompt.

    Args:
        suggestions: List of OCR suggestions

    Returns:
        Formatted string describing suggestions
    """
    if not suggestions:
        return ""

    lines = []
    for sugg in suggestions:
        suggestion_str = ", ".join(sugg.suggestions[:3])  # Limit to 3
        lines.append(
            f"- '{sugg.word}' -> possibly '{suggestion_str}' "
            f"(confidence: {sugg.confidence:.2f}, reason: {sugg.reason})\n"
            f"  Context: {sugg.context}"
        )
    return "\n".join(lines)


def _generate_ocr_observations(
    decisions: list[OCRDecision],
    job_id: str,
) -> list[Observation]:
    """Generate observations from OCR decisions.

    Creates closed observations for fixes and open observations for uncertain.

    Args:
        decisions: OCR decisions from the agent
        job_id: Job ID

    Returns:
        List of observations
    """
    observations = []

    for decision in decisions:
        if decision.decision == "fix":
            observations.append(Observation(
                job_id=job_id,
                agent="structure",
                visual_description=f"OCR detected '{decision.word}' in text",
                markup_description=f"Corrected to '{decision.replacement}'",
                location=ObservationLocation(
                    location_type="region",
                    value=f"OCR correction: {decision.word} -> {decision.replacement}",
                    page_num=1,
                ),
                confidence=0.9,
                severity="minor",
                category="ocr",
                status="closed",
                resolution="fixed",
                human_comment=decision.reasoning,
            ))
        elif decision.decision == "uncertain":
            observations.append(Observation(
                job_id=job_id,
                agent="structure",
                visual_description=f"Potential OCR error: '{decision.word}'",
                markup_description="Uncertain if correction needed",
                location=ObservationLocation(
                    location_type="region",
                    value=f"Potential OCR error: {decision.word}",
                    page_num=1,
                ),
                confidence=0.5,
                severity="minor",
                category="ocr",
                status="open",
                resolution=None,
                human_comment=decision.reasoning,
            ))
        # "keep" decisions don't need observations - the word was correct

    return observations


__all__ = [
    "fix_structural_issues",
    "StructureFixResult",
    "StructureFixOutput",
    "OCRDecision",
    "get_agent",
]
