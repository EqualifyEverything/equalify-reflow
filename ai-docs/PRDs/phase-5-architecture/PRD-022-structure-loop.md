# PRD-022: Structure Verification Loop (Phase 3a: Refine)

## Overview
**Epic**: Phase 5 - Architecture Refactor
**Phase**: Phase 3a: Refine (Structure Loop)
**Estimated Effort**: 3 days
**Dependencies**: PRD-021 (Data Models)
**Reference**: [PRD-020](./PRD-020-3-phase-architecture.md)

## Problem Statement

The current structure agents (alignment, reading order) produce observations that go through consolidation, losing context. The new approach:

1. Run structure verification as a **validation-driven loop** in Phase 3 (Refine)
2. Use **deterministic tools** (linter, spell checker) for detection
3. LLM only decides **in context** - especially for OCR errors
4. Loop until markdown passes lint or max iterations reached

This ensures structurally correct markdown before specialized agents run (Phase 3b).

## Success Criteria

- [ ] Structure loop runs in Phase 3a: Refine (after Phase 2: Extract, before Phase 3b specialized agents)
- [ ] Markdown linter detects formatting issues
- [ ] Spell checker flags OCR errors for LLM review (never auto-corrects)
- [ ] mdformat auto-fixes formatting only
- [ ] Reading order verified per-page on complex layouts
- [ ] Loop exits when lint clean or max 3 iterations
- [ ] StructureTrace captures all corrections for glass box

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Phase 3a: STRUCTURE VERIFICATION LOOP (Refine)              │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐                                           │
│  │ Structure    │ LLM verifies reading order + headings     │
│  │ Agent        │ against page images                       │
│  └──────┬───────┘                                           │
│         ↓                                                   │
│  ┌──────────────┐                                           │
│  │ Markdown     │ Python linter detects issues:             │
│  │ Lint         │ • Malformed headings                      │
│  │ (Python)     │ • Broken structure                        │
│  └──────┬───────┘ • Table formatting                        │
│         ↓         • List consistency                        │
│  ┌──────────────┐                                           │
│  │ Spell Check  │ Python detects OCR-like errors            │
│  │ (Python)     │ using key_terms from manifest             │
│  └──────┬───────┘ (NEVER auto-corrects - flags for LLM)     │
│         ↓                                                   │
│     Issues found?                                           │
│         │                                                   │
│    Yes  │  No                                               │
│    ↓    └──────────────────────────────────────────→ Done   │
│  ┌──────────────┐                                           │
│  │ mdformat     │ Python auto-fixes formatting ONLY:        │
│  │ (Python)     │ • Spacing, blank lines                    │
│  └──────┬───────┘ • NOT content/spelling                    │
│         ↓                                                   │
│  ┌──────────────┐                                           │
│  │ LLM Fix      │ Agent fixes semantic issues:              │
│  │ (Haiku)      │ • OCR errors (decides in context)         │
│  └──────┬───────┘ • Structural issues                       │
│         ↓                                                   │
│      Loop (max 3 iterations)                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## Technical Requirements

### New Dependencies

```toml
# pyproject.toml additions
[project.dependencies]
symspellpy = ">=6.7.0"
pymarkdownlnt = ">=0.9.0"
mdformat = ">=0.7.0"
```

### OCR Checker Implementation

```python
# src/utils/ocr_checker.py

from symspellpy import SymSpell, Verbosity
from pydantic import BaseModel


class OCRSuggestion(BaseModel):
    """Potential OCR error flagged for LLM review."""
    word: str
    suggestions: list[str]
    confidence: float
    reason: str  # "key_term_variant", "spell_check_suggests_key_term"
    context: str  # Surrounding text for LLM context


class OCRChecker:
    """Zero-cost OCR error detection using spell checking."""

    COMMON_CONFUSIONS = [
        ("l", "1", "I"),      # lowercase L, one, uppercase I
        ("O", "0"),           # uppercase O, zero
        ("rn", "m"),          # rn looks like m
        ("cl", "d"),          # cl looks like d
        ("vv", "w"),          # vv looks like w
        ("ii", "u"),          # ii looks like u
    ]

    def __init__(self):
        self.sym_spell = SymSpell(max_dictionary_edit_distance=2)
        # Load frequency dictionary (bundled with symspellpy)
        self.sym_spell.load_dictionary(
            pkg_resources.resource_filename(
                "symspellpy", "frequency_dictionary_en_82_765.txt"
            ),
            term_index=0,
            count_index=1,
        )
        self.key_term_variants: dict[str, str] = {}

    def load_key_terms(self, key_terms: list[str]) -> None:
        """Add document-specific terms as known words."""
        for term in key_terms:
            # Add term with very high frequency (always valid)
            self.sym_spell.create_dictionary_entry(term, 1_000_000)

        # Generate OCR-confused variants for detection
        self.key_term_variants = self._generate_variants(key_terms)

    def _generate_variants(self, terms: list[str]) -> dict[str, str]:
        """Generate OCR-confused variants of key terms."""
        variants = {}
        for term in terms:
            # Apply common confusions to generate variants
            for confusion in self.COMMON_CONFUSIONS:
                for char in confusion:
                    for replacement in confusion:
                        if char != replacement and char in term.lower():
                            variant = term.lower().replace(char, replacement)
                            if variant != term.lower():
                                variants[variant] = term
        return variants

    def check_text(
        self,
        text: str,
        key_terms: list[str],
    ) -> list[OCRSuggestion]:
        """Find potential OCR errors - NEVER auto-corrects, only flags."""

        self.load_key_terms(key_terms)
        suggestions = []

        for word in self._extract_words(text):
            # Skip very short words and numbers
            if len(word) < 3 or word.isdigit():
                continue

            # Check 1: Is this a confused key term?
            if word.lower() in self.key_term_variants:
                suggestions.append(OCRSuggestion(
                    word=word,
                    suggestions=[self.key_term_variants[word.lower()]],
                    confidence=0.95,
                    reason="key_term_variant",
                    context=self._get_context(text, word),
                ))
                continue

            # Check 2: Spell check suggests a key term
            spell_suggestions = self.sym_spell.lookup(
                word,
                Verbosity.CLOSEST,
                max_edit_distance=2,
            )

            if spell_suggestions:
                top_suggestion = spell_suggestions[0].term
                if top_suggestion.lower() != word.lower():
                    # Check if suggestion is a key term
                    for term in key_terms:
                        if term.lower() == top_suggestion.lower():
                            suggestions.append(OCRSuggestion(
                                word=word,
                                suggestions=[term],
                                confidence=0.90,
                                reason="spell_check_suggests_key_term",
                                context=self._get_context(text, word),
                            ))
                            break

        return suggestions

    def _extract_words(self, text: str) -> list[str]:
        """Extract words from text."""
        import re
        return re.findall(r'\b[a-zA-Z]+\b', text)

    def _get_context(self, text: str, word: str, window: int = 50) -> str:
        """Get surrounding context for a word."""
        idx = text.find(word)
        if idx == -1:
            return ""
        start = max(0, idx - window)
        end = min(len(text), idx + len(word) + window)
        return f"...{text[start:end]}..."
```

### Structure Loop Service

```python
# src/services/structure_loop.py

from pymarkdownlnt import PyMarkdownApi
import mdformat

from src.agents.structure.structure_fix_agent import fix_structural_issues
from src.utils.ocr_checker import OCRChecker, OCRSuggestion
from src.shared.models.agent_trace import AgentTrace
from src.shared.models.auto_correction import AutoCorrection


class StructureLoopResult(BaseModel):
    """Output of structure verification loop."""
    markdown: str
    trace: "StructureTrace"


class StructureTrace(BaseModel):
    """Glass box trace of structure loop."""
    iterations: int
    lint_issues_found: int
    lint_issues_fixed: int
    ocr_suggestions_processed: int
    corrections: list[StructureCorrection]
    final_lint_clean: bool
    observations: list[Observation]


class StructureCorrection(BaseModel):
    """Single correction made during structure loop."""
    type: str  # "format", "heading", "ocr", "reading_order"
    description: str
    iteration: int
    auto: bool  # True if mdformat, False if LLM


class StructureLoop:
    """Validation-driven structure correction loop."""

    MAX_ITERATIONS = 3

    def __init__(self):
        self.linter = PyMarkdownApi()
        self.ocr_checker = OCRChecker()

    async def run(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
    ) -> StructureLoopResult:
        """Run validation loop until clean or max iterations."""

        corrections: list[StructureCorrection] = []
        observations: list[Observation] = []
        current_markdown = markdown
        total_lint_issues = 0
        total_ocr_suggestions = 0

        for iteration in range(self.MAX_ITERATIONS):
            # Step 1: Detect issues (Python - free)
            lint_issues = self._run_lint(current_markdown)
            ocr_suggestions = self.ocr_checker.check_text(
                current_markdown,
                manifest.summary.key_entities if manifest.summary else [],
            )
            heading_issues = self._check_heading_hierarchy(current_markdown, manifest)
            reading_order_issues = await self._check_reading_order(
                current_markdown, pages, manifest
            )

            total_lint_issues += len(lint_issues)
            total_ocr_suggestions += len(ocr_suggestions)

            # Step 2: Auto-fix formatting ONLY (mdformat - free)
            formatting_issues = [i for i in lint_issues if i.rule in FORMATTING_RULES]
            if formatting_issues:
                formatted = mdformat.text(current_markdown)
                if formatted != current_markdown:
                    corrections.append(StructureCorrection(
                        type="format",
                        description=f"Fixed {len(formatting_issues)} formatting issues",
                        iteration=iteration,
                        auto=True,
                    ))
                    current_markdown = formatted

            # Step 3: Collect semantic issues for LLM
            semantic_issues = [i for i in lint_issues if i.rule not in FORMATTING_RULES]
            all_semantic = semantic_issues + heading_issues + reading_order_issues

            # Step 4: LLM fixes semantic issues (OCR decisions in context)
            if all_semantic or ocr_suggestions:
                current_markdown, llm_corrections, llm_observations = await self._llm_fix(
                    current_markdown,
                    all_semantic,
                    ocr_suggestions,
                    pages,
                    manifest,
                    iteration,
                )
                corrections.extend(llm_corrections)
                observations.extend(llm_observations)

            # Step 5: Re-check - if clean, exit loop
            remaining_lint = self._run_lint(current_markdown)
            if not remaining_lint:
                break

        # Build trace
        trace = StructureTrace(
            iterations=iteration + 1,
            lint_issues_found=total_lint_issues,
            lint_issues_fixed=total_lint_issues - len(self._run_lint(current_markdown)),
            ocr_suggestions_processed=total_ocr_suggestions,
            corrections=corrections,
            final_lint_clean=len(self._run_lint(current_markdown)) == 0,
            observations=observations,
        )

        return StructureLoopResult(markdown=current_markdown, trace=trace)

    def _run_lint(self, markdown: str) -> list[LintIssue]:
        """Run markdown linter."""
        try:
            results = self.linter.scan_string(markdown)
            return [
                LintIssue(
                    rule=r.rule_id,
                    message=r.rule_description,
                    line=r.line_number,
                )
                for r in results
                if r.rule_id in ENABLED_LINT_RULES
            ]
        except Exception:
            return []  # Graceful degradation

    def _check_heading_hierarchy(
        self,
        markdown: str,
        manifest: DocumentManifest,
    ) -> list[HeadingIssue]:
        """Pure Python heading validation."""
        issues = []
        headings = self._extract_headings(markdown)

        # Check: Only one H1
        h1_count = sum(1 for h in headings if h.level == 1)
        if h1_count > 1:
            issues.append(HeadingIssue(
                type="multiple_h1",
                description=f"Found {h1_count} H1 headings, should have only 1",
                locations=[h.line for h in headings if h.level == 1],
            ))

        # Check: No skipped levels
        prev_level = 0
        for heading in headings:
            if heading.level > prev_level + 1:
                issues.append(HeadingIssue(
                    type="skipped_level",
                    description=f"Skipped from H{prev_level} to H{heading.level}",
                    locations=[heading.line],
                ))
            prev_level = heading.level

        return issues

    async def _check_reading_order(
        self,
        markdown: str,
        pages: list[PageData],
        manifest: DocumentManifest,
    ) -> list[ReadingOrderIssue]:
        """Verify reading order on complex layout pages."""
        issues = []

        for page_num, features in enumerate(manifest.page_features, 1):
            # Only check complex layouts
            if features.layout_type not in ("two_column", "multi_column"):
                continue
            if features.complexity_score < 0.5:
                continue

            page_image = pages[page_num - 1].image_base64
            page_text = self._extract_page_text(markdown, page_num)

            result = await self._verify_page_reading_order(
                page_num=page_num,
                page_image=page_image,
                page_text=page_text,
                layout_type=features.layout_type,
                manifest=manifest,
            )

            if not result.is_correct:
                issues.append(ReadingOrderIssue(
                    page_num=page_num,
                    description=result.issue_description,
                    suggested_fix=result.suggested_fix,
                    confidence=result.confidence,
                ))

        return issues

    async def _llm_fix(
        self,
        markdown: str,
        issues: list,
        ocr_suggestions: list[OCRSuggestion],
        pages: list[PageData],
        manifest: DocumentManifest,
        iteration: int,
    ) -> tuple[str, list[StructureCorrection], list[Observation]]:
        """LLM fixes semantic issues with full context."""

        corrections = []
        observations = []

        # Build prompt with full context
        result = await fix_structural_issues(
            markdown=markdown,
            issues=issues,
            ocr_suggestions=ocr_suggestions,
            manifest=manifest,
            pages=pages,
        )

        # Process results
        if result.corrected_markdown != markdown:
            corrections.append(StructureCorrection(
                type="semantic",
                description=result.correction_summary,
                iteration=iteration,
                auto=False,
            ))

        observations.extend(result.observations)

        return result.corrected_markdown, corrections, observations


# Lint rules configuration
ENABLED_LINT_RULES = {
    # Headings
    "MD001",  # Heading levels increment by one
    "MD002",  # First heading should be H1
    "MD003",  # Heading style consistency
    "MD022",  # Headings surrounded by blank lines

    # Lists
    "MD004",  # Unordered list style consistency
    "MD005",  # List indentation consistency
    "MD030",  # Spaces after list markers

    # Code
    "MD031",  # Fenced code blocks surrounded by blank lines
    "MD040",  # Fenced code blocks should have language

    # Tables
    "MD055",  # Table pipe style
    "MD056",  # Table column count

    # General
    "MD009",  # Trailing spaces
    "MD012",  # Multiple consecutive blank lines
    "MD047",  # Files should end with newline
}

FORMATTING_RULES = {
    # These can be safely auto-fixed by mdformat
    "MD009", "MD012", "MD022", "MD031", "MD047",
}
```

### Structure Fix Agent

```python
# src/agents/structure/structure_fix_agent.py

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from src.agents.core import create_agent, run_agent
from src.agents.dependencies import AgentDependencies
from src.agents.model_tiers import ModelTier


class StructureFixOutput(BaseModel):
    """Output from structure fix agent."""
    corrected_markdown: str
    correction_summary: str
    ocr_decisions: list[OCRDecision]
    observations: list[Observation]


class OCRDecision(BaseModel):
    """Decision on an OCR suggestion."""
    word: str
    decision: str  # "fix", "keep", "uncertain"
    replacement: str | None
    reasoning: str


_agent: Agent[AgentDependencies, StructureFixOutput] | None = None


def get_agent() -> Agent[AgentDependencies, StructureFixOutput]:
    global _agent
    if _agent is None:
        _agent = create_agent(
            prompts_file="structure_fix.yaml",
            output_type=StructureFixOutput,
            model_tier=ModelTier.EFFICIENT,  # Haiku
            use_deps=True,
        )
        _register_dynamic_instructions(_agent)
    return _agent


def _register_dynamic_instructions(agent: Agent) -> None:
    @agent.instructions
    def document_context(ctx: RunContext[AgentDependencies]) -> str:
        if ctx.deps.manifest and ctx.deps.manifest.summary:
            summary = ctx.deps.manifest.summary
            return f"""
<document_context>
Title: {summary.title}
Type: {summary.document_type}
Topic: {summary.topic_summary}
Key terms (IMPORTANT - these are correct spellings): {', '.join(summary.key_entities)}
</document_context>"""
        return ""


async def fix_structural_issues(
    markdown: str,
    issues: list,
    ocr_suggestions: list[OCRSuggestion],
    manifest: DocumentManifest,
    pages: list[PageData],
) -> StructureFixOutput:
    """Fix structural issues with full context."""

    agent = get_agent()

    prompt = f"""
Review and fix these issues in the markdown.

STRUCTURAL ISSUES:
{_format_issues(issues)}

POTENTIAL OCR ERRORS (decide based on context - do NOT auto-fix):
{_format_ocr_suggestions(ocr_suggestions)}

For each OCR suggestion:
- FIX if clearly wrong in document context
- KEEP if might be intentional (proper noun, technical term, etc.)
- Mark UNCERTAIN if you cannot determine

IMPORTANT: The key terms listed in document_context are the CORRECT spellings.
If you see a word that looks similar but slightly different, it's likely an OCR error.

Current markdown:
{markdown}

Return the corrected markdown with your decisions.
"""

    deps = AgentDependencies(
        job_id="structure-fix",
        manifest=manifest,
    )

    result = await run_agent(
        agent=agent,
        prompt=prompt,
        deps=deps,
        job_id="structure-fix",
        agent_name="structure_fix",
    )

    return result.output
```

## Integration with Processing Service

```python
# src/services/processing_service.py (modifications)

async def process_document(...):
    # Phase 1: Analyze
    manifest = await analyze_document(...)

    # Phase 2: Extract
    extraction_result = await extract_document(...)

    # Phase 3a: Refine - Structure Verification Loop
    structure_loop = StructureLoop()
    structure_result = await structure_loop.run(
        markdown=extraction_result.markdown,
        pages=pages,
        manifest=manifest,
    )

    # Use structurally-correct markdown for specialized agents
    full_markdown = structure_result.markdown

    # Track structure trace for final output
    structure_trace = structure_result.trace

    # Phase 3b: Refine - Specialized Agents (using full_markdown)
    # ... figures, tables, typography agents ...

    # Phase 4: Assemble
    # ... assembly service ...
```

## Acceptance Criteria

### Lint Integration
- [ ] pymarkdownlnt scans markdown correctly
- [ ] Only enabled rules are checked
- [ ] Results mapped to LintIssue model

### Spell Check
- [ ] symspellpy detects misspellings
- [ ] Key terms from manifest added to dictionary
- [ ] OCR confusion variants generated
- [ ] Suggestions include context for LLM

### Auto-Fix
- [ ] mdformat fixes formatting issues
- [ ] Content/spelling NEVER auto-fixed
- [ ] Changes tracked in corrections log

### LLM Fix
- [ ] Agent receives full document context
- [ ] OCR decisions made in context
- [ ] Structural issues fixed
- [ ] Observations generated for glass box

### Loop Logic
- [ ] Loop exits when lint clean
- [ ] Max 3 iterations enforced
- [ ] Graceful degradation on errors

## Deliverables

### Files to Create
```
src/utils/
├── ocr_checker.py

src/services/
├── structure_loop.py

src/agents/structure/
├── structure_fix_agent.py

config/agents/
├── structure_fix.yaml

tests/unit/utils/
├── test_ocr_checker.py

tests/unit/services/
├── test_structure_loop.py
```

### Files to Modify
```
src/services/processing_service.py  # Add Phase 3a call
pyproject.toml                      # Add new dependencies
```

## Definition of Done

- [ ] OCRChecker implementation complete
- [ ] StructureLoop service working
- [ ] Structure fix agent responding correctly
- [ ] Integration with processing service
- [ ] Unit tests passing
- [ ] Lint rules configured appropriately
- [ ] OCR never auto-corrects (verified)
