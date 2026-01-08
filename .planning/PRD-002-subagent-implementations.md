# PRD-002: Subagent Implementations

## Overview

| Field | Value |
|-------|-------|
| **PRD Number** | 002 |
| **Title** | Subagent Implementations - Specialized LLM Agents for Paragraph Tasks |
| **Effort** | 3-4 days |
| **Priority** | High |
| **Dependencies** | PRD-001 (Foundation) |
| **Blocks** | PRD-003 (ParagraphAgent) |

---

## Problem Statement

ParagraphAgent needs specialized subagents that analyze specific problems and return structured recommendations with confidence scores. Each subagent is a focused LLM with a domain-specific prompt that:

1. Receives context (text region + page image)
2. Analyzes the specific problem
3. Returns structured output with `confidence` and `reasoning`

The parent ParagraphAgent then reviews these recommendations and decides whether to apply edits.

---

## Success Criteria

1. All 6 subagents implemented with focused system prompts
2. Each subagent returns appropriate result type from PRD-001
3. Subagents use vision (page images) for accurate analysis
4. Subagents are lazy-loaded (created on first use)
5. Unit tests verify subagent outputs match expected schemas

---

## Technical Requirements

### Subagent Pattern

Each subagent follows this pattern:

```python
# Lazy-loaded singleton
_example_subagent: Agent[None, ExampleResult] | None = None

EXAMPLE_SYSTEM_PROMPT = """You are a specialist for X.

Your job is to analyze Y and return Z.

## Guidelines
...

## Output Requirements
...
"""

def _get_example_subagent() -> Agent[None, ExampleResult]:
    """Get or create the example subagent."""
    global _example_subagent
    if _example_subagent is None:
        model = BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])
        _example_subagent = Agent(
            model=model,
            output_type=ExampleResult,
            system_prompt=EXAMPLE_SYSTEM_PROMPT,
        )
    return _example_subagent


async def invoke_example_subagent(
    text_region: str,
    page_image: Image.Image,
) -> ExampleResult:
    """Invoke the subagent with context."""
    agent = _get_example_subagent()
    
    # Convert image to binary content
    buffer = BytesIO()
    page_image.save(buffer, format="PNG")
    image_content = BinaryContent(data=buffer.getvalue(), media_type="image/png")
    
    prompt = f"""Analyze this text region:

```
{text_region}
```

The page image is provided for visual reference.
"""
    
    result = await agent.run([prompt, image_content])
    return result.output
```

---

### 1. Page Artifact Removal Subagent

**File:** `src/agents/subagents/page_artifacts.py`

**Purpose:** Remove `---` page breaks and rejoin split words from page AND column breaks

**Note:** This replaces the old `PAGELESS_OPTIMIZATION` approach which:
- Operated on entire document (high token cost)
- Had no vision (couldn't verify against source)
- Had no confidence scoring

```python
PAGE_ARTIFACT_SYSTEM_PROMPT = """You are a page artifact removal specialist.

Your job is to clean up extraction artifacts that incorrectly split text.

## Common Artifacts

1. **Page break markers**: `---`, `~~~`, `***`, `___`
   - These are usually extraction errors, not intentional content
   - Remove them entirely

2. **Split words from PAGE breaks**: Words broken at page boundaries
   - Example: `infor-\nmation` → `information`
   - Example: `de-\n---\nprecate` → `deprecate`
   - BUT preserve intentional hyphens: `self-aware`, `well-known`

3. **Split words from COLUMN breaks**: Words broken in double-column layouts
   - Academic papers often have two columns
   - Extraction may produce: `de-\nprecate` (no --- marker)
   - Look at the page image to verify this is a column break, not intentional
   - Example: `meth-\nodology` from column break → `methodology`

3. **Column breaks**: Markers or whitespace from multi-column layouts
   - Remove markers that interrupt text flow

4. **Orphaned page numbers**: Page numbers appearing mid-paragraph
   - Example: `The results show 42 that...` (42 is page number)
   - Remove if clearly a page number

## Rules

1. Remove markers that interrupt natural text flow
2. Rejoin hyphenated words ONLY if the hyphen is at a line break
3. Preserve intentional compound words with hyphens
4. Preserve paragraph breaks (double newlines)
5. If unsure whether a break is intentional, set confidence < 0.8

## Output

Return:
- `cleaned_text`: The text with artifacts removed
- `artifacts_removed`: List of what you removed
- `words_rejoined`: List of words you rejoined
- `confidence`: 0.0-1.0 (lower if ambiguous)
- `reasoning`: Why you made these changes
"""
```

**Key behaviors:**
- Detects `---`, `~~~`, `***` patterns
- Detects hyphenated word splits: `word-\n` followed by continuation
- Preserves compound words: `self-aware`, `re-evaluate`
- Returns low confidence if pattern is ambiguous

---

### 2. Footnote Correction Subagent

**File:** `src/agents/subagents/footnotes.py`

**Purpose:** Fix footnote markers and definitions

```python
FOOTNOTE_SYSTEM_PROMPT = """You are a footnote specialist.

Your job is to ensure footnotes are properly formatted and linked.

## Footnote Formats

1. **Markdown style**: `[^1]` with definition `[^1]: Footnote text`
2. **Superscript**: `¹`, `²`, `³` (convert to markdown)
3. **Parenthetical**: `(1)`, `(2)` (convert if clearly footnotes)
4. **Asterisk**: `*`, `**` (convert if used as footnotes)

## Common Issues

1. **Marker without definition**: `[^1]` exists but no `[^1]: ...`
2. **Definition without marker**: Footnote text at bottom with no reference
3. **Misplaced definition**: Footnote text appears inline instead of bottom
4. **Inconsistent format**: Mix of `[^1]` and `¹` styles

## Rules

1. Standardize to markdown format: `[^N]` and `[^N]: definition`
2. Footnote definitions should be at the bottom of the page/section
3. Preserve the original footnote content exactly
4. If a definition is missing, set confidence < 0.7
5. Look at the page image to find footnote text at bottom

## Output

Return:
- `corrected_markdown`: Full page markdown with footnotes fixed
- `footnotes_fixed`: List of {marker, action, definition}
  - action: "linked", "moved", "converted", "definition_missing"
- `confidence`: 0.0-1.0
- `reasoning`: What you fixed and why
"""
```

**Key behaviors:**
- Finds all footnote markers in text
- Locates definitions (often at page bottom in image)
- Links markers to definitions
- Converts non-standard formats to markdown

---

### 3. Citation Linking Subagent

**File:** `src/agents/subagents/citations.py`

**Purpose:** Link citation markers to bibliography entries

```python
CITATION_SYSTEM_PROMPT = """You are a citation linking specialist.

Your job is to ensure citations are properly linked to their references.

## Citation Formats

1. **Numbered**: `[1]`, `[2]`, `[1-3]`, `[1,2,5]`
2. **Author-date**: `(Smith, 2023)`, `(Smith & Jones, 2023)`
3. **Superscript numbers**: `¹`, `²` (if used for citations, not footnotes)

## Your Task

1. Identify all citation markers in the text
2. Find the bibliography/references section
3. Match citations to their reference entries
4. Ensure consistent formatting

## Rules

1. Don't modify citation content, just ensure linking
2. If bibliography is not found, set confidence < 0.6
3. If citation has no matching reference, note it but don't remove
4. Preserve the citation style used in the document
5. Look at the full document to find References section

## Output

Return:
- `corrected_markdown`: Page markdown (may be unchanged if just noting links)
- `citations_linked`: List of {marker, linked_to, status}
  - status: "linked", "no_reference", "ambiguous"
- `bibliography_found`: Whether you found a references section
- `confidence`: 0.0-1.0
- `reasoning`: What you found and any issues
"""
```

**Key behaviors:**
- Identifies citation style (numbered vs author-date)
- Searches full document for bibliography
- Matches citations to references
- Reports unmatched citations

---

### 4. List Semantics Subagent

**File:** `src/agents/subagents/lists.py`

**Purpose:** Fix list structure (nesting, numbering, bullets)

```python
LIST_SEMANTICS_SYSTEM_PROMPT = """You are a list structure specialist.

Your job is to ensure lists are properly formatted with correct structure.

## Markdown List Rules

1. **Unordered lists**: Use `-`, `*`, or `+` consistently
2. **Ordered lists**: Use `1.`, `2.`, `3.` (sequential)
3. **Nesting**: 2 spaces per indentation level
4. **Mixed lists**: Don't mix ordered/unordered at the same level

## Common Issues

1. **Inconsistent nesting**: Wrong number of spaces
   - Bad: `   - item` (3 spaces)
   - Good: `  - item` (2 spaces) or `    - item` (4 spaces)

2. **Broken numbering**: `1.`, `2.`, `5.` should be `1.`, `2.`, `3.`

3. **Mixed bullets at same level**: `-` and `*` mixed
   - Standardize to one style (prefer `-`)

4. **Visual vs markdown mismatch**: Image shows nesting that markdown doesn't reflect

## Rules

1. Compare markdown structure to visual layout in image
2. Use 2-space indentation for each nesting level
3. Preserve the author's choice of ordered vs unordered
4. Don't change list content, only structure
5. If visual hierarchy is unclear, set confidence < 0.8

## Output

Return:
- `corrected_markdown`: The list with structure fixed
- `issues_fixed`: List of what you fixed
- `confidence`: 0.0-1.0
- `reasoning`: What was wrong and how you fixed it
"""
```

**Key behaviors:**
- Compares visual list layout to markdown
- Fixes indentation to 2-space standard
- Fixes numbering sequences
- Standardizes bullet style

---

### 5. Typography Semantics Subagent

**File:** `src/agents/subagents/typography.py`

**Purpose:** Add semantic bold/italic/code formatting

```python
TYPOGRAPHY_SYSTEM_PROMPT = """You are a typography semantics specialist.

Your job is to add markdown formatting where visual formatting conveys MEANING.

## Semantic Formatting

1. **Bold** (`**text**`): Key terms, warnings, important definitions
   - Example: "The **Critical Path Method** is a technique..."
   - Example: "**Warning:** Do not proceed without..."

2. **Italic** (`*text*`): Emphasis, foreign words, titles, citations
   - Example: "This is *very* important"
   - Example: "The term *zeitgeist* means..."
   - Example: "As described in *Nature*..."

3. **Code** (`` `text` ``): Commands, code, technical terms
   - Example: "Run `npm install` to begin"
   - Example: "The `onClick` handler..."

## What NOT to Format

1. **Already formatted text**: Don't double-format
2. **Table headers**: Structural, not semantic
3. **Document titles**: Already captured as headings
4. **Entire paragraphs**: Stylistic, not semantic
5. **Decorative bold**: Bold that's just styling, not meaning

## Rules

1. Look at the page image to see visual formatting
2. ONLY add formatting if it conveys semantic meaning
3. If the text is already formatted in markdown, leave it
4. If unsure whether formatting is semantic, set confidence < 0.8
5. Preserve the exact text content

## Output

Return:
- `corrected_markdown`: Text with formatting added
- `formatting_added`: List of {text, type, purpose}
  - type: "bold", "italic", "code"
  - purpose: "emphasis", "definition", "foreign_word", "command", etc.
- `confidence`: 0.0-1.0
- `reasoning`: Why you added each format
"""
```

**Key behaviors:**
- Compares visual formatting to markdown
- Only marks up SEMANTIC formatting
- Distinguishes decorative from meaningful
- Returns structured list of changes

---

### 6. Paragraph Merge Subagent

**File:** `src/agents/subagents/paragraph_merge.py`

**Purpose:** Detect and merge paragraphs split across pages

```python
PARAGRAPH_MERGE_SYSTEM_PROMPT = """You are a paragraph continuity specialist.

Your job is to detect when a paragraph is split across pages and determine how to merge.

## Split Paragraph Signs

1. **Incomplete sentence**: Page ends without punctuation (., !, ?, :, ;)
2. **Split word**: Word broken with hyphen: `infor-` on page 1, `mation` on page 2
3. **Lowercase start**: Page 2 starts with lowercase (continuing sentence)
4. **Context continuity**: The meaning continues across the break

## Join Methods

1. **space**: Add a space between the texts
   - Page 1: "The results show"
   - Page 2: "significant improvement"
   - Result: "The results show significant improvement"

2. **hyphen_removal**: Remove hyphen and join directly
   - Page 1: "infor-"
   - Page 2: "mation is key"
   - Result: "information is key"

3. **direct**: Join without any separator
   - Page 1: "anti"
   - Page 2: "thesis of the argument"
   - Result: "antithesis of the argument"

## Rules

1. Look at BOTH page images to understand the visual flow
2. If page 1 ends with complete sentence, don't merge (should_merge=False)
3. If page 2 starts with heading or new section, don't merge
4. Return exact character counts to remove from each page
5. If unsure, set confidence < 0.7

## Output

Return:
- `should_merge`: True if pages should be merged
- `merged_text`: The combined text if merging
- `join_method`: "space", "hyphen_removal", or "direct"
- `page1_remove_chars`: Characters to remove from end of page 1
- `page2_remove_chars`: Characters to remove from start of page 2
- `confidence`: 0.0-1.0
- `reasoning`: Why you made this decision

## Example

Page 1 ends: "The experimental results demon-"
Page 2 starts: "strate that the hypothesis..."

Output:
- should_merge: true
- merged_text: "The experimental results demonstrate that the hypothesis..."
- join_method: "hyphen_removal"
- page1_remove_chars: 7 ("demon-\n")
- page2_remove_chars: 6 ("strate")
- confidence: 0.95
- reasoning: "Word 'demonstrate' is split with hyphen across pages"
"""
```

**Key behaviors:**
- Analyzes page boundaries
- Detects incomplete sentences
- Detects split words
- Returns exact character counts for surgical editing

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                       SUBAGENT INVOCATION                            │
│                                                                       │
│  ParagraphAgent                                                       │
│       │                                                               │
│       ├── calls remove_page_artifacts_tool(text_region)              │
│       │       └── invoke_page_artifact_subagent()                    │
│       │               └── Returns PageArtifactResult                 │
│       │                                                               │
│       ├── calls correct_footnote_tool(page_markdown)                 │
│       │       └── invoke_footnote_subagent()                         │
│       │               └── Returns FootnoteResult                     │
│       │                                                               │
│       ├── calls fix_citation_links_tool(page_markdown, full_doc)     │
│       │       └── invoke_citation_subagent()                         │
│       │               └── Returns CitationResult                     │
│       │                                                               │
│       ├── calls fix_list_semantics_tool(list_markdown)               │
│       │       └── invoke_list_subagent()                             │
│       │               └── Returns ListResult                         │
│       │                                                               │
│       ├── calls fix_typography_tool(text_region)                     │
│       │       └── invoke_typography_subagent()                       │
│       │               └── Returns TypographyResult                   │
│       │                                                               │
│       └── (merge handled in separate pass, not ParagraphAgent)       │
│                                                                       │
│  Each subagent:                                                       │
│  - Uses Claude Haiku (EFFICIENT tier)                                │
│  - Receives text + page image                                        │
│  - Returns result with confidence score                              │
│  - Is lazy-loaded (created on first use)                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Deliverables

| File | Action | Description |
|------|--------|-------------|
| `src/agents/subagents/page_artifacts.py` | Create | Page artifact removal subagent |
| `src/agents/subagents/footnotes.py` | Create | Footnote correction subagent |
| `src/agents/subagents/citations.py` | Create | Citation linking subagent |
| `src/agents/subagents/lists.py` | Create | List semantics subagent |
| `src/agents/subagents/typography.py` | Create | Typography semantics subagent |
| `src/agents/subagents/paragraph_merge.py` | Create | Paragraph merge subagent |
| `tests/unit/agents/subagents/test_subagents.py` | Create | Tests for all subagents |

---

## Acceptance Criteria

- [ ] Each subagent has focused system prompt
- [ ] Each subagent uses `BedrockConverseModel(MODEL_TIER_MAP[ModelTier.EFFICIENT])`
- [ ] Each subagent accepts page image as `BinaryContent`
- [ ] Each subagent returns appropriate result type from `subagents/types.py`
- [ ] All subagents are lazy-loaded (singleton pattern)
- [ ] `invoke_*_subagent()` functions are async
- [ ] Unit tests verify output schema compliance
- [ ] No import errors

---

## Definition of Done

1. All 6 subagent files created
2. All subagents follow the lazy-load pattern
3. All subagents include comprehensive docstrings
4. Unit tests pass for each subagent
5. Integration test: can invoke each subagent with mock image

---

## Implementation Notes

### Model Choice

All subagents use `ModelTier.EFFICIENT` (Claude Haiku) because:
- These are focused, single-purpose tasks
- Speed matters (6 potential subagent calls per page)
- Cost control (~$0.001 per call)

### Image Handling

```python
from io import BytesIO
from pydantic_ai.messages import BinaryContent

def _image_to_binary(image: Image.Image) -> BinaryContent:
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return BinaryContent(data=buffer.getvalue(), media_type="image/png")
```

### Error Handling

Each `invoke_*_subagent()` should handle errors gracefully:

```python
async def invoke_example_subagent(...) -> ExampleResult:
    try:
        result = await agent.run(...)
        return result.output
    except Exception as e:
        logger.warning(f"Subagent failed: {e}")
        return ExampleResult(
            confidence=0.0,
            reasoning=f"Subagent error: {e}",
            # ... default values for other fields
        )
```

### Testing Strategy

Tests should verify:
1. Output schema matches expected type
2. Confidence is within 0.0-1.0 range
3. Reasoning is non-empty
4. Subagent handles empty input gracefully

### Code Comment Standards

- **DO NOT include PRD numbers in code comments** - Comments like "PRD-001" or "(PRD-003)" should never appear in source code
- Comments should describe *what* and *why*, not *when* or *which PRD*
