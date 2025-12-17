"""Enhanced Typography Agent with document-type rules and OCR integration.

This module implements the Typography Agent for Phase 3b: Refine (Specialized Agents).
It analyzes typography and formatting, outputting AgentResult with auto_corrections
and review_items based on confidence thresholds.

Key features:
- Document-type-specific formatting rules via @agent.instructions
- OCR suggestion review with context-based decisions
- Bold/italic verification against page images
- 0.95 confidence threshold for auto-corrections

Reference: PRD-025 Typography Agent Enhancement
"""

from __future__ import annotations

import base64
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, BinaryContent, RunContext

from src.agents.dependencies import AgentDependencies
from src.agents.factory import create_agent, extract_usage, run_agent
from src.agents.model_tiers import ModelTier
from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.review_checklist import ReviewItem, ReviewOption

if TYPE_CHECKING:
    from src.services.pdf_converter import PageData
    from src.shared.models.remediation import DocumentManifest
    from src.utils.ocr_checker import OCRSuggestion

logger = logging.getLogger(__name__)


# =============================================================================
# Output Models
# =============================================================================


class FormattingIssue(BaseModel):
    """Formatting issue found by typography agent."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original_text: str = Field(..., description="Text as it appears in markdown")
    corrected_text: str = Field(..., description="Suggested corrected text")
    suggested_format: str = Field(
        ..., description="Format type: bold, italic, monospace"
    )
    page_num: int = Field(..., ge=1)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Why this formatting is needed")
    context: str = Field(..., description="Surrounding text for context")


class OCRDecision(BaseModel):
    """Agent's decision on an OCR suggestion."""

    word: str = Field(..., description="The word being evaluated")
    decision: str = Field(..., description="fix, keep, or uncertain")
    correction: str | None = Field(None, description="Corrected text if decision=fix")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = Field(..., description="Why this decision was made")


class TypographyAnalysisOutput(BaseModel):
    """Output from typography analysis LLM call."""

    formatting_issues: list[FormattingIssue] = Field(
        default_factory=list,
        description="Formatting issues found (bold/italic/monospace)"
    )
    confirmed_ocr_fixes: dict[str, OCRDecision] = Field(
        default_factory=dict,
        description="OCR errors confirmed for auto-fix (word -> decision)"
    )
    uncertain_ocr: dict[str, OCRDecision] = Field(
        default_factory=dict,
        description="OCR errors needing human review (word -> decision)"
    )
    summary: str = Field(
        default="",
        description="Human-readable summary of analysis"
    )


# =============================================================================
# Typography Agent
# =============================================================================


class TypographyAgent:
    """Enhanced typography agent with document-type rules and OCR integration.

    This agent analyzes typography and formatting in documents, comparing
    page images against markdown to detect:
    - Bold text not marked as **bold**
    - Italic text not marked as *italic*
    - Code/monospace text not in `backticks`
    - Formatting inconsistencies

    It also reviews OCR suggestions from the Structure Loop (PRD-022) and
    decides whether to fix, keep, or flag for human review based on
    document context.

    Output is AgentResult with:
    - auto_corrections: High confidence fixes (>=0.95)
    - review_items: Uncertain items for human review (<0.95)
    - observations: All detected issues for glass box transparency

    Example:
        >>> agent = TypographyAgent()
        >>> result = await agent.process(
        ...     markdown=extracted_markdown,
        ...     pages=page_images,
        ...     manifest=document_manifest,
        ...     ocr_suggestions=ocr_checker_results,
        ...     job_id="job-123"
        ... )
        >>> print(f"Auto-corrections: {len(result.auto_corrections)}")
        >>> print(f"Items for review: {len(result.review_items)}")
    """

    CONFIDENCE_THRESHOLD = 0.95
    MAX_PAGES = 5  # Limit pages to avoid token overflow
    MAX_MARKDOWN_LENGTH = 10000  # Characters

    def __init__(self) -> None:
        """Initialize the typography agent."""
        self._agent: Agent[AgentDependencies, TypographyAnalysisOutput] | None = None

    def _get_agent(self) -> Agent[AgentDependencies, TypographyAnalysisOutput]:
        """Get or create the PydanticAI agent (lazy initialization)."""
        if self._agent is None:
            self._agent = create_agent(
                prompts_file="typography_enhanced.yaml",
                output_type=TypographyAnalysisOutput,
                model_tier=ModelTier.EFFICIENT,
                use_deps=True,
                max_retries=2,
            )
            self._register_dynamic_instructions(self._agent)
            logger.info("TypographyAgent initialized with EFFICIENT tier (Haiku)")
        return self._agent

    @staticmethod
    def get_document_type_rules(deps: AgentDependencies) -> str:
        """Get document-type-specific formatting rules.

        Extracted as static method for testability.
        """
        if not deps.manifest:
            return ""

        doc_type = deps.manifest.document_type
        key_terms: list[str] = []

        # Get key terms from summary if available
        if deps.manifest.summary:
            key_terms = deps.manifest.summary.key_entities[:5]

        rules = {
            "research_paper": f"""
<document_type_rules type="research_paper">
For research papers:
- Technical terms are often italicized on first use
- Project names ({', '.join(key_terms) if key_terms else 'check context'}) should be formatted consistently
- Code, commands, file paths should be in monospace `backticks`
- Watch for OCR errors in Greek letters and math notation
- Citations typically appear as (Author, Year) or [1]
- Bold is used sparingly for emphasis
</document_type_rules>""",
            "syllabus": """
<document_type_rules type="syllabus">
For syllabi:
- Dates and deadlines are often bolded for emphasis
- Course codes should be consistent (e.g., CS 101)
- Book titles should be italicized
- Policy headers are often bold
- Times and locations may be bold
</document_type_rules>""",
            "exam": """
<document_type_rules type="exam">
For exams:
- Question numbers and point values are often bolded
- Instructions sections need clear formatting
- Code snippets should be in monospace
- Answer blanks (___) should be preserved exactly
- Section headers often bold
</document_type_rules>""",
            "lecture_notes": """
<document_type_rules type="lecture_notes">
For lecture notes:
- Key concepts are often bolded
- Code examples should be in monospace
- Informal structure is acceptable
- Bullet points are common
- Less strict formatting rules
</document_type_rules>""",
            "textbook_chapter": """
<document_type_rules type="textbook_chapter">
For textbook chapters:
- New terms are often bolded or italicized on first use
- Definitions may be in bold
- Code and technical notation in monospace
- Cross-references like "See Chapter 3" should be preserved
- Examples and exercises may have special formatting
</document_type_rules>""",
        }

        return rules.get(doc_type, "<document_type_rules>Standard document formatting applies.</document_type_rules>")

    @staticmethod
    def get_ocr_suggestions_context(deps: AgentDependencies) -> str:
        """Get OCR suggestions context for agent review.

        Extracted as static method for testability.
        """
        ocr_suggestions: list[Any] = deps.custom_context.get("ocr_suggestions", [])

        if not ocr_suggestions:
            return ""

        # Limit suggestions to avoid token bloat
        suggestions = ocr_suggestions[:10]

        suggestions_text = "\n".join(
            f"- '{s.word}' might be '{s.suggestions[0] if s.suggestions else '?'}' "
            f"(reason: {s.reason}, context: ...{s.context[:60]}...)"
            for s in suggestions
        )

        key_terms: list[str] = []
        if deps.manifest and deps.manifest.summary:
            key_terms = deps.manifest.summary.key_entities

        return f"""
<ocr_suggestions>
POTENTIAL OCR ERRORS (detected by spell checker, need your verification):
{suggestions_text}

IMPORTANT: The following are CORRECT spellings (key terms from this document):
{', '.join(key_terms) if key_terms else 'None identified'}

For each OCR suggestion above:
- Mark as "fix" if it's CLEARLY wrong in context
- Mark as "keep" if it might be intentional (proper noun, technical term, etc.)
- Mark as "uncertain" if you cannot determine with confidence
</ocr_suggestions>"""

    @staticmethod
    def get_document_context(deps: AgentDependencies) -> str:
        """Get document-level context.

        Extracted as static method for testability.
        """
        if not deps.manifest:
            return ""

        summary_info = ""
        if deps.manifest.summary:
            summary = deps.manifest.summary
            summary_info = f"""
Topic: {summary.topic_summary}
Key entities: {', '.join(summary.key_entities[:5])}
Audience: {summary.audience_level}"""

        return f"""
<document_context>
Title: {deps.manifest.document_title}
Type: {deps.manifest.document_type}
Total pages: {deps.manifest.total_pages}{summary_info}
</document_context>"""

    def _register_dynamic_instructions(
        self,
        agent: Agent[AgentDependencies, TypographyAnalysisOutput],
    ) -> None:
        """Register dynamic instruction generators for context injection."""

        @agent.instructions
        def document_type_rules(ctx: RunContext[AgentDependencies]) -> str:
            """Inject document-type-specific formatting rules."""
            return TypographyAgent.get_document_type_rules(ctx.deps)

        @agent.instructions
        def ocr_suggestions_context(ctx: RunContext[AgentDependencies]) -> str:
            """Inject OCR suggestions for agent review."""
            return TypographyAgent.get_ocr_suggestions_context(ctx.deps)

        @agent.instructions
        def document_context(ctx: RunContext[AgentDependencies]) -> str:
            """Provide document-level context."""
            return TypographyAgent.get_document_context(ctx.deps)

        logger.debug("TypographyAgent: Dynamic instructions registered")

    async def process(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        ocr_suggestions: list[OCRSuggestion],
        job_id: str,
    ) -> AgentResult:
        """Process typography and return unified AgentResult.

        Args:
            markdown: Structurally correct markdown from Phase 1.5
            pages: Page images for visual comparison
            manifest: Document manifest with DocumentSummary
            ocr_suggestions: OCR suggestions from OCRChecker (Phase 1.5)
            job_id: Job identifier for tracking

        Returns:
            AgentResult with auto_corrections, review_items, and observations
        """
        start_time = datetime.now(UTC)
        observations: list[Observation] = []
        auto_corrections: list[AutoCorrection] = []
        review_items: list[ReviewItem] = []
        total_cost = 0.0

        agent = self._get_agent()

        # Build analysis prompt (with truncation warning if needed)
        if len(markdown) > self.MAX_MARKDOWN_LENGTH:
            logger.warning(
                f"Job {job_id}: Markdown truncated from {len(markdown)} to "
                f"{self.MAX_MARKDOWN_LENGTH} chars for typography analysis"
            )
        truncated_markdown = markdown[:self.MAX_MARKDOWN_LENGTH]
        prompt = f"""
Analyze typography and formatting in this document.

Compare the page images to the markdown and identify:
1. Bold text in image not marked as **bold** in markdown
2. Italic text in image not marked as *italic* in markdown
3. Code/monospace text not in `backticks`
4. Formatting inconsistencies

Document type: {manifest.document_type}

Markdown to analyze:
```markdown
{truncated_markdown}
```

Analyze the formatting and provide your findings.
"""

        # Create dependencies with OCR suggestions in custom_context
        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            document_type=manifest.document_type,
            custom_context={"ocr_suggestions": ocr_suggestions},
        )

        # Build multimodal prompt with page images
        prompt_parts: list[Any] = [prompt]
        pages_to_process = pages[:self.MAX_PAGES]

        for page in pages_to_process:
            if page.image_base64:
                try:
                    image_bytes = base64.b64decode(page.image_base64)
                    prompt_parts.append(
                        BinaryContent(data=image_bytes, media_type="image/png")
                    )
                except Exception as e:
                    logger.warning(f"Job {job_id}: Failed to decode image for page {page.page_num}: {e}")
            else:
                logger.warning(
                    f"Job {job_id}: No image data for page {page.page_num}, "
                    "skipping typography analysis for this page"
                )

        # Run the agent
        try:
            result = await run_agent(
                agent=agent,
                prompt=prompt_parts,
                deps=deps,
                job_id=job_id,
                agent_name="typography_enhanced",
                model_tier=ModelTier.EFFICIENT,
            )

            usage = extract_usage(result, ModelTier.EFFICIENT)
            total_cost += usage.estimated_cost_cents

            output: TypographyAnalysisOutput = result.output

            # Process formatting issues
            for issue in output.formatting_issues:
                obs = self._create_formatting_observation(issue, job_id)
                observations.append(obs)

                if issue.confidence >= self.CONFIDENCE_THRESHOLD:
                    auto_corrections.append(
                        self._create_auto_correction(issue, obs.id)
                    )
                else:
                    review_items.append(
                        self._create_formatting_review_item(issue, obs.id)
                    )

            # Process confirmed OCR fixes
            for word, decision in output.confirmed_ocr_fixes.items():
                obs = self._create_ocr_observation(
                    word, decision, markdown, pages, job_id
                )
                observations.append(obs)

                if decision.correction:
                    auto_corrections.append(
                        self._create_ocr_auto_correction(
                            word, decision, obs.id, obs.location.page_num
                        )
                    )

            # Process uncertain OCR
            for word, decision in output.uncertain_ocr.items():
                # Find original suggestion for context
                original_suggestion = next(
                    (s for s in ocr_suggestions if s.word == word),
                    None
                )

                obs = self._create_ocr_observation(
                    word, decision, markdown, pages, job_id
                )
                observations.append(obs)

                review_items.append(
                    self._create_ocr_review_item(
                        word, decision, original_suggestion, obs.id, markdown, pages
                    )
                )

            reasoning_summary = output.summary or self._generate_summary(
                output, auto_corrections, review_items
            )

        except Exception as e:
            logger.error(f"TypographyAgent failed: {e}", exc_info=True)
            reasoning_summary = f"Typography analysis failed: {e}"

        end_time = datetime.now(UTC)

        return AgentResult(
            agent_name="typography",
            observations=observations,
            auto_corrections=auto_corrections,
            review_items=review_items,
            reasoning_summary=reasoning_summary,
            confidence=self._calculate_confidence(auto_corrections, review_items),
            enhanced_content=None,  # Typography doesn't enhance placeholders
            cost_cents=total_cost,
            time_seconds=(end_time - start_time).total_seconds(),
        )

    # =========================================================================
    # Helper Methods: Observation Creation
    # =========================================================================

    def _create_formatting_observation(
        self,
        issue: FormattingIssue,
        job_id: str,
    ) -> Observation:
        """Create observation for a formatting issue."""
        return Observation(
            id=str(uuid.uuid4()),
            job_id=job_id,
            agent="typography",
            source="agent",
            visual_description=f"Text should be {issue.suggested_format}",
            markup_description=f"Currently: '{issue.original_text[:50]}...' (plain text)",
            location=ObservationLocation(
                location_type="range",
                value=issue.original_text[:50],
                page_num=issue.page_num,
            ),
            confidence=issue.confidence,
            severity="minor",
            category="formatting",
        )

    def _create_ocr_observation(
        self,
        word: str,
        decision: OCRDecision,
        markdown: str,
        pages: list[PageData],
        job_id: str,
    ) -> Observation:
        """Create observation for an OCR issue."""
        page_num = self._find_page(markdown, word, pages)
        description = (
            f"Should be: '{decision.correction}'"
            if decision.correction
            else "Needs verification"
        )

        return Observation(
            id=str(uuid.uuid4()),
            job_id=job_id,
            agent="typography",
            source="agent",
            visual_description=f"Possible OCR error: '{word}'",
            markup_description=description,
            location=ObservationLocation(
                location_type="range",
                value=word,
                page_num=page_num,
            ),
            confidence=decision.confidence,
            severity="minor",
            category="ocr",
        )

    # =========================================================================
    # Helper Methods: AutoCorrection Creation
    # =========================================================================

    def _create_auto_correction(
        self,
        issue: FormattingIssue,
        observation_id: str,
    ) -> AutoCorrection:
        """Create auto-correction for a formatting issue."""
        return AutoCorrection(
            id=str(uuid.uuid4()),
            observation_id=observation_id,
            search=issue.original_text,
            replace=issue.corrected_text,
            justification=issue.reasoning,
            confidence=issue.confidence,
            agent="typography",
            page_num=issue.page_num,
        )

    def _create_ocr_auto_correction(
        self,
        word: str,
        decision: OCRDecision,
        observation_id: str,
        page_num: int,
    ) -> AutoCorrection:
        """Create auto-correction for a confirmed OCR fix."""
        return AutoCorrection(
            id=str(uuid.uuid4()),
            observation_id=observation_id,
            search=word,
            replace=decision.correction or word,
            justification=f"OCR error correction: {decision.reasoning}",
            confidence=decision.confidence,
            agent="typography",
            page_num=page_num,
        )

    # =========================================================================
    # Helper Methods: ReviewItem Creation
    # =========================================================================

    def _create_formatting_review_item(
        self,
        issue: FormattingIssue,
        observation_id: str,
    ) -> ReviewItem:
        """Create review item for uncertain formatting issue."""
        return ReviewItem(
            id=str(uuid.uuid4()),
            observation_id=observation_id,
            agent="typography",
            question=f"Should '{issue.original_text[:30]}...' be formatted as {issue.suggested_format}?",
            options=[
                ReviewOption(
                    id="accept",
                    label=f"Yes, format as {issue.suggested_format}",
                    action="replace",
                    replacement_text=issue.corrected_text,
                    is_recommended=True,
                ),
                ReviewOption(
                    id="keep",
                    label="No, keep as plain text",
                    action="keep",
                    is_recommended=False,
                ),
            ],
            search_text=issue.original_text,
            context=issue.context,
            page_num=issue.page_num,
            agent_recommendation=issue.reasoning,
            agent_confidence=issue.confidence,
        )

    def _create_ocr_review_item(
        self,
        word: str,
        decision: OCRDecision,
        original_suggestion: OCRSuggestion | None,
        observation_id: str,
        markdown: str,
        pages: list[PageData],
    ) -> ReviewItem:
        """Create review item for uncertain OCR issue."""
        suggested_correction = (
            original_suggestion.suggestions[0]
            if original_suggestion and original_suggestion.suggestions
            else ""
        )
        context = (
            original_suggestion.context
            if original_suggestion
            else self._get_context(markdown, word)
        )
        page_num = self._find_page(markdown, word, pages)

        return ReviewItem(
            id=str(uuid.uuid4()),
            observation_id=observation_id,
            agent="typography",
            question=f"Is '{word}' a typo?",
            options=[
                ReviewOption(
                    id="fix",
                    label=f"Yes, replace with '{suggested_correction}'",
                    action="replace",
                    replacement_text=suggested_correction,
                    is_recommended=(decision.decision == "fix"),
                ),
                ReviewOption(
                    id="keep",
                    label=f"No, '{word}' is correct",
                    action="keep",
                    is_recommended=(decision.decision == "keep"),
                ),
            ],
            search_text=word,
            context=context,
            page_num=page_num,
            agent_recommendation=decision.reasoning,
            agent_confidence=decision.confidence,
        )

    # =========================================================================
    # Helper Methods: Utilities
    # =========================================================================

    def _find_page(
        self,
        markdown: str,
        word: str,
        pages: list[PageData],
    ) -> int:
        """Find which page a word is on based on page markers."""
        # Find page markers and word position
        page_markers = list(re.finditer(r"<!-- Page (\d+) -->", markdown))
        word_pos = markdown.find(word)

        if word_pos == -1:
            return 1

        current_page = 1
        for marker in page_markers:
            if marker.start() > word_pos:
                break
            current_page = int(marker.group(1))

        return current_page

    def _get_context(self, text: str, word: str, window: int = 100) -> str:
        """Get surrounding context for a word."""
        idx = text.find(word)
        if idx == -1:
            return ""
        start = max(0, idx - window)
        end = min(len(text), idx + len(word) + window)
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end]}{suffix}"

    def _calculate_confidence(
        self,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> float:
        """Calculate overall confidence score."""
        if not auto_corrections and not review_items:
            return 1.0

        all_confidences = [c.confidence for c in auto_corrections]
        all_confidences.extend([r.agent_confidence for r in review_items])

        return sum(all_confidences) / len(all_confidences) if all_confidences else 0.5

    def _generate_summary(
        self,
        output: TypographyAnalysisOutput,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> str:
        """Generate human-readable summary of analysis."""
        parts = []

        if output.formatting_issues:
            parts.append(f"{len(output.formatting_issues)} formatting issues found")

        if output.confirmed_ocr_fixes:
            parts.append(f"{len(output.confirmed_ocr_fixes)} OCR errors auto-fixed")

        if output.uncertain_ocr:
            parts.append(f"{len(output.uncertain_ocr)} OCR items need review")

        if auto_corrections:
            parts.append(f"{len(auto_corrections)} auto-corrections applied")

        if review_items:
            parts.append(f"{len(review_items)} items for human review")

        return ". ".join(parts) if parts else "No typography issues found."


# Module-level convenience function for backward compatibility
async def analyze(
    pages: list[PageData],
    manifest: DocumentManifest,
    markdown: str,
    job_id: str,
    ocr_suggestions: list[OCRSuggestion] | None = None,
) -> AgentResult:
    """Analyze typography and return AgentResult.

    This is a convenience function that creates a TypographyAgent
    and calls process().

    Args:
        pages: Page images for visual comparison
        manifest: Document manifest
        markdown: Markdown to analyze
        job_id: Job identifier
        ocr_suggestions: Optional OCR suggestions from OCRChecker

    Returns:
        AgentResult with observations, auto_corrections, and review_items
    """
    agent = TypographyAgent()
    return await agent.process(
        markdown=markdown,
        pages=pages,
        manifest=manifest,
        ocr_suggestions=ocr_suggestions or [],
        job_id=job_id,
    )


__all__ = [
    "FormattingIssue",
    "OCRDecision",
    "TypographyAgent",
    "TypographyAnalysisOutput",
    "analyze",
]
