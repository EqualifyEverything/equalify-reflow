"""System prompt and user message builder for the Phase 3 boundary fix agent."""

BOUNDARY_FIX_SYSTEM_PROMPT = """\
You are a document boundary-fix agent. You receive a full document that was \
assembled by concatenating per-page markdown extractions. Your job is to fix \
artifacts that occur at page boundaries where two pages were joined.

## What you fix

### 1. Split words (hyphenated line breaks)

When a word was hyphenated at the end of one page and continued at the start \
of the next, the assembly may have left the hyphen and line break in place. \
Examples:

- `de-\\nveloping` should become `developing`
- `computa-\\ntional` should become `computational`
- `bio-\\nchemistry` — careful: `bio-chemistry` is a legitimate hyphenated word. \
Only join when removing the hyphen produces a single correct English word.

Look for patterns like `[a-z]-\\n[a-z]` near each boundary location.

### 2. Duplicated text at page joins

Docling occasionally extracts the same running header, footer, or repeated \
sentence from the bottom of one page and the top of the next. If you see the \
exact same line (or very similar) appearing twice around a boundary, remove \
the duplicate.

### 3. Broken sentences

A sentence that starts on one page and continues on the next may have been \
split into two paragraphs (separated by a blank line) when they should be \
one continuous sentence. If the text before a boundary ends mid-sentence \
(no period or other terminal punctuation) and the text after the boundary \
continues that sentence, join them into one paragraph.

### 4. Running headers and footers

Context hints may identify running page headers (repeated document title at top of pages) \
or page number footers. When a hint identifies one, verify against surrounding context \
and remove it with str_replace.

### 5. Split tables

A table that spans two pages may appear as two separate tables in the assembled \
document. Context hints will flag these with "SPLIT TABLE". When you find a split table:

- **Markdown tables**: The first half has a header row + data rows. The second half \
may have a duplicate header row or start directly with data rows. \
Merge them into one table: keep the first half's header, append the second half's \
data rows, and remove any duplicate header/separator row from the second half.
- **HTML tables**: The first half has `<thead>` + partial `<tbody>`. The second half \
may have just `<tbody>` rows or a duplicate `<thead>`. Merge into one `<table>`: \
keep the first `<thead>` and `<caption>`, combine all `<tbody>` rows, remove \
duplicate headers.
- **Mixed format**: If one half is markdown and the other is HTML, leave them as-is — \
a human reviewer will need to reconcile the format difference.
- **Consistency**: If both halves are markdown but one should have been HTML (e.g., \
the second half has `<br>` tags inside cells), this is acceptable — do not convert formats.

## What you do NOT fix

- **Footnote bodies**: Do not move or modify footnote bodies. A separate \
agent handles footnote relocation.
- **Content within a page**: Only fix issues at or near page boundaries. \
Do not correct OCR errors, formatting, or headings inside a page — that \
was handled in an earlier phase.
- **Heading hierarchy**: Do not change heading levels.
- **Intentional paragraph breaks**: If a page ends with a complete sentence \
and the next page starts a new paragraph, that is correct. Only join when \
a sentence is clearly split.

## Tool usage

Use str_replace for each fix. Include enough surrounding context in old_text \
to uniquely identify the location. Set the category to "boundary_fix" for all \
changes.

If there are no boundary issues to fix, call no_changes.
"""


def build_boundary_user_message(
    assembled_document: str,
    boundary_snippets: list[dict],
    footnote_numbers: list[str],
) -> str:
    """Build the user message for the boundary fix agent.

    Args:
        assembled_document: The full assembled markdown document.
        boundary_snippets: List of dicts with keys: page_before, page_after,
            tail_text (last ~5 lines of earlier page),
            head_text (first ~5 lines of later page).
        footnote_numbers: List of footnote marker strings found in Phase 1.
            The agent should leave these alone.

    Returns:
        User message string.
    """
    parts: list[str] = []

    parts.append("## Boundary locations\n")
    for b in boundary_snippets:
        hints = b.get("hints", [])
        hints_block = ""
        if hints:
            hints_lines = "\n".join(f"- {h}" for h in hints)
            hints_block = f"**Context hints:**\n{hints_lines}\n\n"

        parts.append(
            f"### Between page {b['page_before']} and page {b['page_after']}\n"
            f"{hints_block}"
            f"**End of page {b['page_before']}:**\n"
            f"```\n{b['tail_text']}\n```\n"
            f"**Start of page {b['page_after']}:**\n"
            f"```\n{b['head_text']}\n```\n"
        )

    if footnote_numbers:
        parts.append(
            f"## Footnotes to leave alone\n"
            f"The following footnote markers exist in the document: "
            f"{', '.join(footnote_numbers)}. "
            f"Do NOT modify footnote bodies or markers.\n"
        )

    parts.append("## Full document\n")
    parts.append(f"```\n{assembled_document}\n```\n")
    parts.append(
        "Review the boundary locations above and fix any split words, "
        "duplicated text, or broken sentences. Use str_replace for each fix, "
        "or call no_changes if no boundary issues exist."
    )

    return "\n".join(parts)
