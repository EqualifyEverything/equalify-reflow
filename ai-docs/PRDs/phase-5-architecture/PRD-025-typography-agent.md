# PRD-025: Typography Agent Enhancement

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: Phase 3b: Refine (Specialized Agents)
**Estimated Effort**: 2 days
**Dependencies**: PRD-021 (Data Models), PRD-022 (Structure Loop - for OCR suggestions)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

The current typography agent has issues:
- Low confidence observations (0.89-0.94)
- No document-type-specific rules
- Can't detect OCR errors effectively
- No visual comparison for bold/italic verification

The enhanced typography agent should:
1. Use **document-type-specific rules** via dynamic prompts
2. **Verify bold/italic** by comparing page images to markdown
3. **Confirm OCR suggestions** from Python pre-check (from Structure Loop)
4. Route to auto_corrections or review_items based on confidence

## Success Criteria

- [ ] Dynamic prompts based on document type
- [ ] OCR suggestions from Python passed to agent for context-based decisions
- [ ] Bold/italic verification against page images
- [ ] AgentResult output with auto_corrections and review_items
- [ ] Glass box reasoning for all decisions

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Typography Agent                                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Input:                                                     │
│  • markdown (structurally correct from Phase 1.5)           │
│  • pages (images for visual comparison)                     │
│  • manifest (with DocumentSummary)                          │
│  • ocr_suggestions (from OCRChecker in Phase 1.5)           │
│                                                             │
│  Dynamic System Prompts:                                    │
│  • @agent.instructions for document_type rules              │
│  • @agent.instructions for OCR suggestions                  │
│                                                             │
│  Analysis:                                                  │
│  1. Compare page images to markdown for bold/italic         │
│  2. Review OCR suggestions with document context            │
│  3. Check formatting consistency                            │
│                                                             │
│  Output: AgentResult                                        │
│  • auto_corrections (high confidence)                       │
│  • review_items (uncertain formatting, OCR)                 │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Technical Requirements

### Typography Agent Implementation

```python
# src/agents/typography/typography_agent.py

from datetime import datetime
import uuid

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, BinaryContent

from src.shared.models.agent_trace import AgentResult
from src.shared.models.auto_correction import AutoCorrection
from src.shared.models.review_checklist import ReviewItem, ReviewOption
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.remediation import DocumentManifest
from src.agents.core import create_agent, run_agent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier
from src.utils.ocr_checker import OCRSuggestion


class FormattingIssue(BaseModel):
    """Formatting issue found by typography agent."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original_text: str
    corrected_text: str
    suggested_format: str  # "bold", "italic", "monospace"
    page_num: int
    confidence: float
    reasoning: str
    context: str


class OCRDecision(BaseModel):
    """Agent's decision on an OCR suggestion."""
    word: str
    decision: str  # "fix", "keep", "uncertain"
    correction: str | None
    confidence: float
    reasoning: str


class TypographyAnalysisOutput(BaseModel):
    """Output from typography analysis."""
    formatting_issues: list[FormattingIssue]
    confirmed_ocr_fixes: dict[str, OCRDecision]  # word -> decision
    uncertain_ocr: dict[str, OCRDecision]  # word -> decision
    summary: str


class TypographyAgent:
    """Enhanced typography agent with document-type rules."""

    CONFIDENCE_THRESHOLD = 0.95

    def __init__(self):
        self._agent = None

    def _get_agent(self) -> Agent[AgentDependencies, TypographyAnalysisOutput]:
        if self._agent is None:
            self._agent = create_agent(
                prompts_file="typography_enhanced.yaml",
                output_type=TypographyAnalysisOutput,
                model_tier=ModelTier.EFFICIENT,
                use_deps=True,
            )
            self._register_dynamic_instructions(self._agent)
        return self._agent

    def _register_dynamic_instructions(
        self,
        agent: Agent[AgentDependencies, TypographyAnalysisOutput]
    ) -> None:
        """Register dynamic instruction generators."""

        @agent.instructions
        def document_type_rules(ctx: RunContext[AgentDependencies]) -> str:
            """Inject document-type-specific formatting rules."""
            if not ctx.deps.manifest or not ctx.deps.manifest.summary:
                return ""

            doc_type = ctx.deps.manifest.document_type
            key_terms = ctx.deps.manifest.summary.key_entities[:5]

            rules = {
                "research_paper": f"""
For research papers:
- Technical terms are often italicized on first use
- Project names ({', '.join(key_terms)}) should be formatted consistently throughout
- Code, commands, file paths should be in monospace `backticks`
- Watch for OCR errors in Greek letters and math notation
- Citations typically appear as (Author, Year) or [1]
- Bold is used sparingly for emphasis
""",
                "syllabus": """
For syllabi:
- Dates and deadlines are often bolded for emphasis
- Course codes should be consistent (e.g., CS 101)
- Book titles should be italicized
- Policy headers are often bold
- Times and locations may be bold
""",
                "exam": """
For exams:
- Question numbers and point values are often bolded
- Instructions sections need clear formatting
- Code snippets should be in monospace
- Answer blanks (___) should be preserved exactly
- Section headers often bold
""",
                "lecture_notes": """
For lecture notes:
- Key concepts are often bolded
- Code examples should be in monospace
- Informal structure is acceptable
- Bullet points are common
- Less strict formatting rules
""",
                "textbook_chapter": """
For textbook chapters:
- New terms are often bolded or italicized on first use
- Definitions may be in bold
- Code and technical notation in monospace
- Cross-references like "See Chapter 3" should be preserved
- Examples and exercises may have special formatting
""",
            }

            return rules.get(doc_type, "Standard document formatting applies.")

        @agent.instructions
        def ocr_suggestions_context(ctx: RunContext[AgentDependencies]) -> str:
            """Inject OCR suggestions for agent review."""
            ocr_suggestions = ctx.deps.custom_context.get("ocr_suggestions", [])

            if not ocr_suggestions:
                return ""

            # Limit to avoid token bloat
            suggestions = ocr_suggestions[:10]

            suggestions_text = "\n".join(
                f"- '{s.word}' might be '{s.suggestions[0]}' "
                f"(reason: {s.reason}, context: ...{s.context[:60]}...)"
                for s in suggestions
            )

            key_terms = []
            if ctx.deps.manifest and ctx.deps.manifest.summary:
                key_terms = ctx.deps.manifest.summary.key_entities

            return f"""
POTENTIAL OCR ERRORS (detected by spell checker, need your verification):
{suggestions_text}

IMPORTANT: The following are CORRECT spellings (key terms from this document):
{', '.join(key_terms)}

For each OCR suggestion above:
- Mark as "fix" if it's CLEARLY wrong in context
- Mark as "keep" if it might be intentional (proper noun, technical term, etc.)
- Mark as "uncertain" if you cannot determine with confidence
"""

    async def process(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
        ocr_suggestions: list[OCRSuggestion],
        job_id: str,
    ) -> AgentResult:
        """Process typography and return unified result."""

        start_time = datetime.utcnow()
        observations: list[Observation] = []
        auto_corrections: list[AutoCorrection] = []
        review_items: list[ReviewItem] = []
        total_cost = 0.0

        agent = self._get_agent()

        # Build prompt for analysis
        prompt = f"""
Analyze typography and formatting in this document.

Compare the page images to the markdown and identify:
1. Bold text in image not marked as **bold** in markdown
2. Italic text in image not marked as *italic* in markdown
3. Code/monospace text not in `backticks`
4. Formatting inconsistencies

Document type: {manifest.document_type if manifest else 'unknown'}

Markdown to analyze:
{markdown[:10000]}  # Limit to avoid token overflow
"""

        # Create dependencies with OCR suggestions
        deps = AgentDependencies(
            job_id=job_id,
            manifest=manifest,
            custom_context={"ocr_suggestions": ocr_suggestions},
        )

        # Include page images for visual comparison
        prompt_parts = [prompt]
        for i, page in enumerate(pages[:5]):  # Limit pages to avoid token overflow
            import base64
            image_bytes = base64.b64decode(page.image_base64)
            prompt_parts.append(BinaryContent(data=image_bytes, media_type="image/png"))

        result = await run_agent(
            agent=agent,
            prompt=prompt_parts,
            deps=deps,
            job_id=job_id,
            agent_name="typography_enhanced",
        )

        from src.agents.factory import extract_usage
        usage = extract_usage(result, ModelTier.EFFICIENT)
        total_cost += usage.estimated_cost_cents

        output: TypographyAnalysisOutput = result.output

        # Process formatting issues
        for issue in output.formatting_issues:
            obs = Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="typography",
                source="agent",
                visual_description=f"Text should be {issue.suggested_format}",
                markup_description=f"Currently: '{issue.original_text}'",
                location=ObservationLocation(
                    location_type="range",
                    value=issue.original_text[:50],
                    page_num=issue.page_num,
                ),
                confidence=issue.confidence,
                severity="minor",
                category="formatting",
            )
            observations.append(obs)

            if issue.confidence >= self.CONFIDENCE_THRESHOLD:
                auto_corrections.append(AutoCorrection(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    search=issue.original_text,
                    replace=issue.corrected_text,
                    justification=issue.reasoning,
                    confidence=issue.confidence,
                    agent="typography",
                    page_num=issue.page_num,
                ))
            else:
                review_items.append(ReviewItem(
                    id=str(uuid.uuid4()),
                    observation_id=obs.id,
                    agent="typography",
                    # NOTE: category is NOT on ReviewItem - derived from Observation at checklist level
                    question=f"Should '{issue.original_text}' be formatted as {issue.suggested_format}?",
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
                    search_text=issue.original_text,  # Text to find for replacement
                    context=issue.context,
                    page_num=issue.page_num,
                    agent_recommendation=issue.reasoning,
                    agent_confidence=issue.confidence,
                ))

        # Process OCR decisions
        for word, decision in output.confirmed_ocr_fixes.items():
            obs = Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="typography",
                source="agent",
                visual_description=f"OCR error: '{word}'",
                markup_description=f"Should be: '{decision.correction}'",
                location=ObservationLocation(
                    location_type="range",
                    value=word,
                    page_num=self._find_page(markdown, word, pages),
                ),
                confidence=decision.confidence,
                severity="minor",
                category="ocr",
            )
            observations.append(obs)

            auto_corrections.append(AutoCorrection(
                id=str(uuid.uuid4()),
                observation_id=obs.id,
                search=word,
                replace=decision.correction,
                justification=f"OCR error correction: {decision.reasoning}",
                confidence=decision.confidence,
                agent="typography",
            ))

        for word, decision in output.uncertain_ocr.items():
            # Find the original suggestion
            original_suggestion = next(
                (s for s in ocr_suggestions if s.word == word),
                None
            )

            obs = Observation(
                id=str(uuid.uuid4()),
                job_id=job_id,
                agent="typography",
                source="agent",
                visual_description=f"Possible OCR error: '{word}'",
                markup_description=f"Might be: '{original_suggestion.suggestions[0] if original_suggestion else 'unknown'}'",
                location=ObservationLocation(
                    location_type="range",
                    value=word,
                    page_num=self._find_page(markdown, word, pages),
                ),
                confidence=decision.confidence,
                severity="minor",
                category="ocr",
            )
            observations.append(obs)

            suggested_correction = original_suggestion.suggestions[0] if original_suggestion else ""

            review_items.append(ReviewItem(
                id=str(uuid.uuid4()),
                observation_id=obs.id,
                agent="typography",
                # NOTE: category is NOT on ReviewItem - derived from Observation at checklist level
                question=f"Is '{word}' a typo?",
                options=[
                    ReviewOption(
                        id="fix",
                        label=f"Yes, replace with '{suggested_correction}'",
                        action="replace",
                        replacement_text=suggested_correction,
                        is_recommended=decision.decision == "fix",
                    ),
                    ReviewOption(
                        id="keep",
                        label=f"No, '{word}' is correct",
                        action="keep",
                        is_recommended=decision.decision == "keep",
                    ),
                ],
                search_text=word,  # Text to find for replacement
                context=original_suggestion.context if original_suggestion else "",
                page_num=self._find_page(markdown, word, pages),
                agent_recommendation=decision.reasoning,
                agent_confidence=decision.confidence,
            ))

        end_time = datetime.utcnow()

        return AgentResult(
            agent_name="typography",
            observations=observations,
            auto_corrections=auto_corrections,
            review_items=review_items,
            reasoning_summary=output.summary,
            confidence=self._calculate_confidence(auto_corrections, review_items),
            enhanced_content=None,  # Typography doesn't enhance placeholders
            cost_cents=total_cost,
            time_seconds=(end_time - start_time).total_seconds(),
        )

    def _find_page(self, markdown: str, word: str, pages: list) -> int:
        """Find which page a word is on."""
        import re

        # Find page markers and word position
        page_markers = list(re.finditer(r'<!-- Page (\d+) -->', markdown))
        word_pos = markdown.find(word)

        if word_pos == -1:
            return 1

        current_page = 1
        for marker in page_markers:
            if marker.start() > word_pos:
                break
            current_page = int(marker.group(1))

        return current_page

    def _calculate_confidence(
        self,
        auto_corrections: list[AutoCorrection],
        review_items: list[ReviewItem],
    ) -> float:
        """Calculate overall confidence."""
        if not auto_corrections and not review_items:
            return 1.0

        all_confidences = [c.confidence for c in auto_corrections]
        all_confidences.extend([r.agent_confidence for r in review_items])

        return sum(all_confidences) / len(all_confidences) if all_confidences else 0.5
```

### Agent Configuration

```yaml
# config/agents/typography_enhanced.yaml

system_prompt: |
  You are a typography and formatting specialist. Your job is to compare
  the visual appearance of a document (from page images) against its
  markdown representation and identify formatting discrepancies.

  Focus on:
  1. Bold text - text that appears heavier/thicker in the image
  2. Italic text - text that appears slanted in the image
  3. Monospace/code text - text with fixed-width font
  4. Formatting consistency - same elements formatted the same way

  Also review OCR error suggestions and decide based on document context.

  Be conservative - only flag clear formatting mismatches, not uncertain cases.

user_prompt_template: |
  {prompt}

  Analyze the formatting and provide your findings.
```

## Acceptance Criteria

### Dynamic Prompts
- [ ] Document type rules injected via @agent.instructions
- [ ] OCR suggestions passed with context
- [ ] Key terms highlighted as correct spellings

### Formatting Detection
- [ ] Bold detection from page images
- [ ] Italic detection from page images
- [ ] Monospace detection for code
- [ ] Consistency checking

### OCR Decisions
- [ ] Agent reviews Python suggestions
- [ ] Decisions made in document context
- [ ] "fix", "keep", "uncertain" routing

### Output
- [ ] Returns AgentResult model
- [ ] High confidence → auto_corrections
- [ ] Uncertain → review_items
- [ ] Glass box reasoning

## Deliverables

### Files to Create/Modify
```
src/agents/typography/
├── typography_agent.py      # REPLACE: Enhanced agent

config/agents/
├── typography_enhanced.yaml  # NEW: Enhanced prompts

tests/unit/agents/typography/
├── test_typography_agent.py  # UPDATE: New tests
```

## Definition of Done

- [ ] TypographyAgent class implemented
- [ ] Dynamic prompts working
- [ ] OCR decisions integrated
- [ ] Returns AgentResult correctly
- [ ] Unit tests passing
