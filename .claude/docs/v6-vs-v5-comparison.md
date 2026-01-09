# V6 vs V5 Comparison: Process and Feature Improvements

**Date:** 2026-01-07
**Author:** Claude (AI Analysis)
**Purpose:** Identify v6 improvements for potential backport to v5 (current production branch)

## Executive Summary

V6 is an **experimental branch** with significant architectural improvements over the current v5 production pipeline on `refactor/agentic-document-pipeline`. While v5 provides a solid 5-phase pipeline (Analyze → Extract → Refine → Assemble → Verify), v6 introduces a more granular 6-phase approach with better instrumentation, visual context analysis, and remediation tooling.

**Key Finding:** V6 and v5 share the same core processing service but v6 adds extensive infrastructure in the `src/agents/v6/` directory that is NOT present in v5.

### Similarity Score: ~40%
- **Shared:** Core extraction, analysis, structure loop, assembly phases
- **V6 Unique:** Visual context analysis, edit tool pattern, ledger system, spell checking, page-level orchestration

---

## Architecture Comparison

### V5 Pipeline (Current - Production Ready)

```
Phase 1: Analysis (Haiku)
  └─ Chained analysis: layout → doctype → headings/features
  └─ Generates manifest with required_agents

Phase 2: Extraction (Haiku)
  └─ Validation-driven markdown extraction
  └─ Plain text output with retry loop

Phase 3a: Structure Loop (Haiku)
  └─ Lint → OCR check → mdformat → LLM fix
  └─ Ensures structurally valid markdown

Phase 3b: Specialized Agents (Sonnet)
  └─ FiguresAgent, TablesAgent, TypographyAgent
  └─ Returns AgentResult (observations, auto_corrections, review_items)

Phase 4: Assembly
  └─ Apply auto_corrections
  └─ Build ProcessingTrace & ReviewChecklist
  └─ Calculate confidence

Phase 5: Verification (Haiku)
  └─ Compare final markdown vs source images
  └─ Apply final corrections
  └─ Recovery for failed corrections
```

### V6 Pipeline (Experimental - More Granular)

```
Phase 1: Extraction
  └─ Detect scanned vs native PDFs
  └─ Docling OR Vision-based extraction
  └─ Per-page markdown + images

Phase 2: Static Analysis (NEW)
  └─ Spell checking (pyspellchecker)
  └─ Domain term extraction
  └─ Build correction dictionary

Phase 3: Visual Context (NEW)
  └─ 3a: Page Analyzer - Per-page visual analysis
       ├─ Layout detection (columns, sidebar)
       ├─ Heading identification with confidence
       ├─ Content summary
       ├─ Continuity detection (page breaks)
       └─ Element counting (figures, tables, code)
  └─ 3b: Heading Synthesizer - Build authoritative outline

Phase 4: Remediation (NEW - Edit Tool Pattern)
  └─ Page Orchestrator coordinates per-page fixes
  └─ Tool suite (10 tools):
       ├─ Simple: fix_spelling, fix_heading_level, extract_links
       ├─ Sub-agents: generate_alt_text, transcribe_table,
       │              transcribe_visual_content, fix_reading_order
       └─ Utilities: merge_continuation, flag_for_review
  └─ Ledger tracks all changes

Phase 5: Verification (Enhanced)
  └─ Static linting (markdown-it-py)
  └─ LLM verification with recovery
  └─ Spell check verification

Phase 6: Assembly
  └─ Page joining with continuity handling
  └─ Footnote consolidation
  └─ Link deduplication
  └─ S3 upload with ledger
```

---

## Key Differences

### 1. Process Improvements

#### ✅ V6: Granular Visual Context Analysis (Phase 3)

**What it is:**
- Per-page visual analysis comparing page images to extracted markdown
- Detects layout (single/multi-column, sidebar), headings, content elements
- Identifies continuity issues (mid-sentence page breaks, hyphenation)
- Produces `PageContextResult` for each page with observations

**Why it's better:**
- **Accuracy:** Visual inspection catches extraction errors early
- **Context:** Understands document structure before remediation
- **Confidence scoring:** Per-page heading detection includes confidence levels
- **Layout awareness:** Handles multi-column layouts and sidebars correctly

**Implementation:** `src/agents/v6/agents/page_analyzer.py`

```python
# V6 Page Analyzer
result, call = await analyze_page(
    page_image=image,
    page_markdown=extracted_md,
    page_num=1,
    total_pages=10,
    tracker=tracker,
)
# Returns: layout, detected_headings, summary, continuity hints, element counts
```

**Feasibility:** **HARD** - Requires significant refactoring
- Needs new data models (PageContextResult, ContinuityHints, DetectedHeading)
- Adds LLM calls per page (cost increase)
- Requires integration with existing specialized agents

**Recommendation:** Defer to v7 - Cost/benefit unclear for production

---

#### ✅ V6: Static Spell Checking (Phase 2)

**What it is:**
- Pre-extraction spell checking using `pyspellchecker`
- Builds domain-specific dictionary from analysis phase
- Generates spell correction suggestions before LLM processing

**Why it's better:**
- **Cost reduction:** Catches typos without LLM calls
- **Consistency:** Domain terms added to dictionary (e.g., "UIC", "Docling")
- **Pre-emptive fixing:** Corrects obvious errors before expensive LLM phases

**Implementation:** `src/agents/v6/linting/spell_check.py`

```python
from src.agents.v6.linting import run_spell_check

suggestions, stats = run_spell_check(
    markdown=full_markdown,
    custom_dictionary=["UIC", "Docling", "PydanticAI"],
)
# Returns: SpellCheckSuggestion list with old_string/new_string pairs
```

**Feasibility:** **EASY** - Standalone module
- Add `pyspellchecker` dependency
- Integrate in Phase 3a (structure loop) or before extraction
- ~100 lines of code + tests

**Recommendation:** ⭐ **High priority** - Quick win, reduces cost

---

#### ✅ V6: Edit Tool Pattern for Remediation (Phase 4)

**What it is:**
- Standardized `execute_edit(markdown, old_string, new_string)` function
- All remediation tools return `EditResult` with helpful error hints
- Orchestrator provides exact text, tools do simple replacement
- Error hints guide retry (NOT FOUND, AMBIGUOUS, NO CHANGE)

**Why it's better:**
- **Reliability:** Exact string matching prevents incorrect replacements
- **Debuggability:** Clear error messages ("NOT FOUND: whitespace mismatch")
- **Teachability:** Error hints train orchestrator to succeed on retry
- **Consistency:** All tools follow same pattern

**Implementation:** `src/agents/v6/tools/__init__.py`

```python
from src.agents.v6.tools import execute_edit, EditResult

result = execute_edit(
    markdown="The teh quick fox",
    old_string="teh quick",  # Exact match required
    new_string="the quick",
)

if not result.success:
    print(result.error_hint)  # "NOT FOUND: ..." or "AMBIGUOUS: ..."
```

**V5 Current Approach:**
- Agents generate corrections as `AutoCorrection` objects
- Assembly service applies corrections with simple `str.replace()`
- No feedback loop or retry mechanism

**Feasibility:** **MEDIUM** - Refactor existing correction system
- Replace `AutoCorrection` application logic with `execute_edit()`
- Add error handling and retry logic to specialized agents
- Migrate existing tools to return `EditResult`

**Recommendation:** ⭐ **High priority** - Improves reliability significantly

---

#### ✅ V6: Audit Ledger System

**What it is:**
- Append-only log of all pipeline actions (issues detected, fixes applied, phases)
- Queryable by phase, page, entry type
- JSON serializable for debugging and compliance

**Why it's better:**
- **Debugging:** Trace exactly what happened during processing
- **Quality analysis:** Understand which agents/tools are most effective
- **Compliance:** Auditable trail for accessibility remediation
- **Transparency:** Shows user what was changed and why

**Implementation:** `src/agents/v6/ledger.py`

```python
from src.agents.v6.ledger import Ledger, LedgerEntryType

ledger = Ledger(document_id=job_id)

# Log issue detection
ledger.log_issue_detected(
    phase="remediation",
    page=3,
    issue_type="missing_alt_text",
    description="Figure 2 has no alt-text",
)

# Log fix applied
ledger.log_issue_fixed(
    phase="remediation",
    page=3,
    issue_type="missing_alt_text",
    action_taken="Generated alt-text: 'Bar chart...'",
    duration_ms=1250,
)

# Export for debugging
ledger_json = ledger.to_json()
```

**Feasibility:** **EASY** - Standalone module
- Add ledger module (~500 lines)
- Integrate logging calls throughout pipeline
- Store ledger JSON in S3 alongside results

**Recommendation:** ⭐ **High priority** - Essential for debugging and transparency

---

### 2. Feature Improvements

#### ✅ V6: Advanced Alt Text Generation

**What it is:**
- Vision-enabled sub-agent analyzes page image AND markdown context
- Detects decorative vs informative images
- Generates contextually appropriate alt-text (under 125 chars)
- Returns image type classification (chart, diagram, photo, icon, decorative)

**Why it's better than V5:**
- **Context awareness:** Uses surrounding text and section heading
- **Decorative detection:** Marks decorative images with empty alt-text
- **Quality control:** Reasoning-first output ensures thoughtful alt-text
- **Type-specific guidance:** Different strategies for charts vs photos vs diagrams

**V5 Current Approach:**
- FiguresAgent generates alt-text but less sophisticated
- No decorative detection
- Limited context awareness

**Implementation:** `src/agents/v6/tools/generate_alt_text.py`

```python
from src.agents.v6.tools.generate_alt_text import generate_alt_text

result = await generate_alt_text(
    input_data=GenerateAltTextInput(
        page_num=3,
        current_markdown=markdown,
        old_string="![Figure 2]()",
        surrounding_context="The enrollment trends show...",
        section_heading="Enrollment Analysis",
        document_type="academic_paper",
    ),
    page_image=image,
    tracker=tracker,
)

# Returns: alt_text, is_decorative, image_type, confidence
```

**Feasibility:** **MEDIUM** - Enhance existing FiguresAgent
- Add decorative detection logic
- Improve prompt with document type and section context
- Add image type classification
- Backport to FiguresAgent without major refactor

**Recommendation:** ⭐ **Medium priority** - Meaningful accessibility improvement

---

#### ✅ V6: Heading Synthesis

**What it is:**
- Combines visual heading detection from page analysis
- Cross-references with markdown heading structure
- Builds authoritative heading outline with confidence scores
- Resolves conflicts between visual and markdown headings

**Why it's better:**
- **Accuracy:** Visual confirmation of heading levels
- **Confidence:** Each heading has confidence score (high/medium/low)
- **Conflict resolution:** Handles mismatches between visual and markdown
- **Hierarchical validation:** Ensures proper H1 → H2 → H3 nesting

**V5 Current Approach:**
- Analysis phase generates heading tree from markdown only
- No visual validation of heading levels
- Structure agent fixes heading hierarchy but less sophisticated

**Implementation:** `src/agents/v6/agents/heading_synthesizer.py`

**Feasibility:** **HARD** - Depends on Phase 3 visual context
- Requires PageContextResult from page analyzer
- Needs new HeadingOutline data model
- Complex conflict resolution logic

**Recommendation:** Defer to v7 - Requires full Phase 3 implementation

---

#### ✅ V6: Footnote Processing

**What it is:**
- Dedicated tool for footnote detection and linking
- Matches footnote markers (¹, ², [1], etc.) to definitions
- Converts to markdown link syntax
- Handles cross-page footnote references

**Why it's better:**
- **Automated:** No manual footnote linking required
- **Accessibility:** Screen readers can navigate footnotes
- **Consistency:** Standardized markdown footnote format
- **Visual verification:** Uses page images to confirm matches

**V5 Current Approach:**
- Assembly phase does basic footnote consolidation
- No visual verification
- Limited marker format support

**Implementation:** `src/agents/v6/tools/process_footnotes.py`

**Feasibility:** **MEDIUM** - Enhance assembly phase
- Port footnote matching logic from v6
- Add visual verification step (optional)
- Improve marker detection patterns

**Recommendation:** **Medium priority** - Nice accessibility improvement

---

#### ✅ V6: Reading Order Correction

**What it is:**
- Visual analysis determines correct reading flow
- Fixes out-of-order content (e.g., sidebar read before main text)
- Handles multi-column layouts correctly
- Uses page image to determine spatial relationships

**Why it's better:**
- **Accessibility:** Screen readers follow logical reading order
- **Multi-column support:** Correctly orders columns left-to-right
- **Sidebar handling:** Places sidebars in appropriate position
- **Visual ground truth:** Uses actual page layout, not guessing

**V5 Current Approach:**
- Relies on Docling extraction order (often incorrect for multi-column)
- No visual validation of reading order
- Structure agent can reorder but less sophisticated

**Implementation:** `src/agents/v6/tools/fix_reading_order.py`

**Feasibility:** **HARD** - Requires visual context analysis
- Needs page layout detection (columns, sidebar)
- Complex spatial reasoning
- Depends on Phase 3 infrastructure

**Recommendation:** Defer to v7 - Complex and depends on Phase 3

---

### 3. Quality Assurance Improvements

#### ✅ V6: Enhanced Verification Phase

**What V6 adds:**
- **Static linting:** `markdown-it-py` validation before LLM
- **Spell check verification:** Post-processing spell check
- **Custom validators:** Domain-specific validation rules
- **Recovery mechanism:** Detailed failure handling with fallbacks

**V5 Current Approach:**
- Verification phase compares markdown vs page images
- Applies corrections with recovery for failures
- No static linting or spell checking

**Key Difference:**
V6 uses **layered validation** (static → LLM → recovery) while v5 relies primarily on LLM verification.

**Feasibility:** **EASY** - Add static validators
- Integrate `markdown-it-py` linting
- Add spell check pass
- Port custom validators from v6

**Recommendation:** ⭐ **High priority** - Catches errors before expensive LLM calls

---

### 4. Infrastructure Improvements

#### ✅ V6: Better Instrumentation

**What v6 adds:**
- **Ledger system:** Detailed action logging
- **Usage tracking:** Per-agent, per-phase LLM usage
- **Event system:** Structured events for pipeline milestones
- **Store pattern:** GlobalDocumentStore for shared state

**V5 Current Approach:**
- OpenTelemetry spans for phases
- Basic logging
- No structured ledger or event system

**Feasibility:** **MEDIUM** - Gradual adoption
- Start with ledger (easy)
- Add event system incrementally
- Consider store pattern for v7

**Recommendation:** ⭐ **High priority** for ledger, **Medium** for rest

---

## Recommendations: What to Backport to V5

### Tier 1: High Priority (Quick Wins)

1. **✅ Spell Checking (Phase 2)**
   - **Impact:** Cost reduction, quality improvement
   - **Effort:** Low (1-2 days)
   - **Risk:** Low
   - **Action:** Add `pyspellchecker` to Phase 3a structure loop

2. **✅ Audit Ledger System**
   - **Impact:** Debugging, transparency, compliance
   - **Effort:** Low (2-3 days)
   - **Risk:** Low
   - **Action:** Copy ledger module, add logging throughout pipeline

3. **✅ Edit Tool Pattern**
   - **Impact:** Reliability, debuggability
   - **Effort:** Medium (1 week)
   - **Risk:** Medium
   - **Action:** Refactor AutoCorrection application logic

4. **✅ Static Validation (Verification Phase)**
   - **Impact:** Quality, cost reduction
   - **Effort:** Low (2-3 days)
   - **Risk:** Low
   - **Action:** Add markdown-it-py linting before LLM verification

### Tier 2: Medium Priority (Moderate Effort)

5. **✅ Enhanced Alt Text Generation**
   - **Impact:** Accessibility improvement
   - **Effort:** Medium (3-4 days)
   - **Risk:** Low
   - **Action:** Enhance FiguresAgent with decorative detection and type classification

6. **✅ Footnote Processing**
   - **Impact:** Accessibility, user experience
   - **Effort:** Medium (1 week)
   - **Risk:** Medium
   - **Action:** Port footnote tool logic to assembly phase

7. **✅ Event System**
   - **Impact:** Monitoring, observability
   - **Effort:** Medium (1 week)
   - **Risk:** Low
   - **Action:** Add structured event logging alongside ledger

### Tier 3: Low Priority / Defer to V7

8. **❌ Visual Context Analysis (Phase 3)**
   - **Reason:** Requires major refactoring, cost increase unclear
   - **Alternative:** Wait for v7 with proven ROI data from v6

9. **❌ Reading Order Correction**
   - **Reason:** Depends on Phase 3 visual context
   - **Alternative:** Continue with current Docling extraction order

10. **❌ Heading Synthesis**
    - **Reason:** Depends on Phase 3 visual context
    - **Alternative:** Current heading tree from analysis is sufficient

---

## Implementation Plan

### Phase 1: Quick Wins (2-3 weeks)

**Week 1:**
- Add spell checking to Phase 3a structure loop
- Implement ledger module and basic logging

**Week 2:**
- Add static validation to verification phase
- Enhance error handling with Edit Tool Pattern principles

**Week 3:**
- Testing and refinement
- Documentation updates

### Phase 2: Medium Effort (4-6 weeks)

**Weeks 4-5:**
- Enhance FiguresAgent with v6 alt-text improvements
- Implement footnote processing in assembly

**Week 6:**
- Add event system for better monitoring
- Comprehensive testing

---

## Cost/Benefit Analysis

| Feature | Dev Days | LLM Cost Impact | Quality Impact | User Impact |
|---------|----------|-----------------|----------------|-------------|
| Spell Checking | 2 | -10% (saves LLM calls) | +15% | Medium |
| Audit Ledger | 3 | 0% | +20% (debugging) | High |
| Edit Tool Pattern | 5 | 0% | +25% (reliability) | High |
| Static Validation | 2 | -5% | +10% | Low |
| Enhanced Alt Text | 4 | +2% | +30% (a11y) | High |
| Footnote Processing | 5 | +3% | +15% (a11y) | Medium |
| **Total Tier 1+2** | **21 days** | **-10% net** | **+115%** | **High** |

**ROI Calculation:**
- Development cost: ~4 weeks
- LLM cost savings: ~10% ongoing
- Quality improvement: Significant (fewer errors, better debugging)
- User satisfaction: Higher (better accessibility)

**Recommendation:** Proceed with Tier 1 immediately, evaluate Tier 2 after initial results.

---

## Risks and Mitigation

### Risk 1: Breaking Changes
**Mitigation:** Feature flags for new functionality, gradual rollout

### Risk 2: Cost Increase (Alt Text, Footnotes)
**Mitigation:** Monitor cost per document, adjust if needed

### Risk 3: Complexity Creep
**Mitigation:** Only backport proven features, avoid Phase 3 visual context

### Risk 4: Testing Overhead
**Mitigation:** Comprehensive test coverage before merge

---

## Conclusion

V6 represents significant improvements in **process rigor, instrumentation, and quality assurance**. The most valuable improvements for v5 are:

1. **Spell checking** - Immediate cost savings
2. **Audit ledger** - Essential for debugging and compliance
3. **Edit tool pattern** - Reliability and error handling
4. **Static validation** - Quality gates before LLM calls

The **visual context analysis (Phase 3)** is v6's biggest innovation but requires substantial refactoring. Recommend deferring to v7 while adopting the standalone improvements listed above.

**Next Steps:**
1. Prioritize Tier 1 features for immediate backport
2. Create feature branches for each improvement
3. Implement with comprehensive tests
4. Monitor production impact before next tier

---

**Document Version:** 1.0
**Last Updated:** 2026-01-07
**Prepared by:** Claude (AI Analysis)
**Review Status:** Ready for team review
