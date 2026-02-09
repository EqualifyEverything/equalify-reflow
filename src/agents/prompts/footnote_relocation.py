"""System prompt and user message builder for the Phase 3 footnote relocation agent."""

from __future__ import annotations

import difflib
import re
import unicodedata

FOOTNOTE_RELOCATION_SYSTEM_PROMPT = """\
You are a footnote relocation agent. You receive a document and a list of \
footnotes that were identified during structure analysis. Your job is to \
convert inline footnotes into markdown endnote syntax.

Each footnote listing includes pre-located context showing exactly where the \
footnote body and inline marker appear in the document. Use these hints to \
write accurate str_replace calls.

## What you do

For each footnote in the provided list:

### 1. Convert the inline reference marker

Find the superscript or inline number in the body text and convert it to \
markdown footnote reference syntax: `[^N]`

Examples:
- `recent work1 has shown` -> `recent work[^1] has shown`
- `recent work\u00b9 has shown` -> `recent work[^1] has shown`
- `the result2,3 suggests` -> `the result[^2][^3] suggests`

The marker may appear as a regular number, a superscript unicode character, \
or already in some partial format. Convert it to `[^N]` syntax.

### 2. Remove the footnote body from its inline position

Footnote bodies typically appear at the bottom of a page, below a horizontal \
rule or separator. They start with the footnote number. Remove the entire \
footnote body text from its current position.

Also remove the horizontal rule or separator line that precedes the footnote \
section if it becomes empty after removing all footnotes from that page.

### 3. Append to endnotes section

After processing all footnotes, append an endnotes section at the end of \
the document in this format:

```
---

## Notes

[^1]: First footnote body text here.

[^2]: Second footnote body text here.
```

## What you do NOT do

- Do not modify any text that is not a footnote marker or footnote body.
- Do not change heading levels, fix OCR errors, or reformat content.
- Do not create footnotes that were not identified by the structure analysis.
- Do not change the numbering of footnotes — use the original numbers.

## Tool usage

Use str_replace for each change:
1. One call to convert each inline marker to `[^N]` syntax.
2. One call to remove each footnote body from its inline position.
3. One final call to append the endnotes section (replace the very end of \
the document with itself plus the endnotes block).

Set the category to "footnote" for all changes.

If there are no footnotes to relocate, call no_changes.
"""

# Unicode superscript digits → ASCII
_SUPERSCRIPT_MAP: dict[str, str] = {
    "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
    "\u2070": "0", "\u2074": "4", "\u2075": "5",
    "\u2076": "6", "\u2077": "7", "\u2078": "8", "\u2079": "9",
}


def _normalize_for_search(text: str) -> str:
    """Normalize text for fuzzy comparison (lowercase, collapse whitespace)."""
    text = unicodedata.normalize("NFKC", text)
    for sup, digit in _SUPERSCRIPT_MAP.items():
        text = text.replace(sup, digit)
    return " ".join(text.lower().split())


def _find_body_context(
    document: str,
    body_text: str,
    number: str,
    context_lines: int = 3,
) -> str | None:
    """Find footnote body in document and return surrounding context.

    Tries exact substring match first, then falls back to line-by-line
    fuzzy matching using SequenceMatcher.

    Returns a context snippet string, or None if not found.
    """
    lines = document.split("\n")
    norm_body = _normalize_for_search(body_text)

    # Strategy 1: look for lines starting with the footnote number
    # (e.g. "1 Smith et al..." or "1. Smith et al...")
    number_prefix_patterns = [
        f"{number} ",    # "1 Smith..."
        f"{number}. ",   # "1. Smith..."
        f"{number}.",    # "1.Smith..."
    ]
    for i, line in enumerate(lines):
        stripped = line.strip()
        for pat in number_prefix_patterns:
            if stripped.startswith(pat):
                # Verify this is the right footnote via fuzzy match on rest
                rest = stripped[len(pat):].strip()
                # Use first 60 chars of body for comparison
                body_start = norm_body[:60]
                rest_norm = _normalize_for_search(rest)[:60]
                if not body_start or not rest_norm:
                    continue
                ratio = difflib.SequenceMatcher(None, body_start, rest_norm).ratio()
                if ratio >= 0.6:
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    snippet = "\n".join(
                        f"  L{j + 1}: {lines[j]}" for j in range(start, end)
                    )
                    return snippet

    # Strategy 2: fuzzy match body text against each line
    best_ratio = 0.0
    best_idx = -1
    body_words = norm_body[:80]
    for i, line in enumerate(lines):
        norm_line = _normalize_for_search(line)
        if len(norm_line) < 10:
            continue
        ratio = difflib.SequenceMatcher(None, body_words, norm_line[:80]).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i

    if best_ratio >= 0.5 and best_idx >= 0:
        start = max(0, best_idx - context_lines)
        end = min(len(lines), best_idx + context_lines + 1)
        snippet = "\n".join(
            f"  L{j + 1}: {lines[j]}" for j in range(start, end)
        )
        return snippet

    return None


def _find_marker_context(
    document: str,
    number: str,
    context_chars: int = 60,
) -> list[str]:
    """Find likely inline marker locations for a footnote number.

    Searches for bare digits, superscript digits, or partial [^N] patterns
    that appear mid-text (not at line start, which is likely the body).

    Returns a list of short context snippets (up to 3).
    """
    results: list[str] = []

    # Build regex for the marker: bare digit or superscript
    # Must be preceded by a letter (not another digit) and NOT followed by a digit
    # to avoid matching "123" when looking for "1"
    superscripts = [k for k, v in _SUPERSCRIPT_MAP.items() if v == number]
    alternatives = [re.escape(number)] + [re.escape(s) for s in superscripts]
    pattern = re.compile(
        r"([a-zA-Z])(" + "|".join(alternatives) + r")(?!\d)(\s|[,;.\)\]]|$)"
    )

    for m in pattern.finditer(document):
        pos = m.start()
        start = max(0, pos - context_chars)
        end = min(len(document), pos + context_chars)
        snippet = document[start:end].replace("\n", " ")
        # Add ellipsis markers
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(document) else ""
        results.append(f"{prefix}{snippet}{suffix}")
        if len(results) >= 3:
            break

    return results


def build_footnote_user_message(
    document: str,
    footnotes: list[dict],
) -> str:
    """Build the user message for the footnote relocation agent.

    Pre-locates each footnote body and inline marker in the document,
    providing surrounding context so the agent can write accurate
    str_replace calls.

    Args:
        document: The full document markdown (post boundary fixes).
        footnotes: List of dicts with keys: number, body_text, source_page.

    Returns:
        User message string.
    """
    parts: list[str] = []

    if not footnotes:
        parts.append("No footnotes were identified. Call no_changes.")
        return "\n".join(parts)

    parts.append(f"## Footnotes to relocate ({len(footnotes)} total)\n")

    for fn in footnotes:
        number = fn["number"]
        body_text = fn["body_text"]
        source_page = fn["source_page"]

        parts.append(f"### Footnote {number} (from page {source_page})")
        parts.append(f"**Body text:** {body_text}\n")

        # Pre-locate body in document
        body_ctx = _find_body_context(document, body_text, number)
        if body_ctx:
            parts.append(f"**Body location in document:**\n```\n{body_ctx}\n```\n")
        else:
            parts.append(
                "**Body location:** Could not pre-locate. "
                "Search the document for text starting with "
                f'"{number}" followed by: "{body_text[:50]}..."\n'
            )

        # Pre-locate inline marker
        marker_snippets = _find_marker_context(document, number)
        if marker_snippets:
            parts.append("**Likely inline marker locations:**")
            for snippet in marker_snippets:
                parts.append(f"  `{snippet}`")
            parts.append("")
        else:
            parts.append(
                f"**Inline marker:** Could not pre-locate marker \"{number}\". "
                "Search for a bare digit or superscript near the content that "
                "references this footnote.\n"
            )

    parts.append("## Full document\n")
    parts.append(f"```\n{document}\n```\n")
    parts.append(
        "## Instructions\n\n"
        "Process each footnote listed above using the pre-located context hints. "
        "For each footnote:\n"
        "1. Convert the inline marker to `[^N]` syntax (use the marker location hints)\n"
        "2. Remove the footnote body from its current position (use the body location hints)\n"
        "3. After all removals, append the endnotes section at the document end\n\n"
        "Use str_replace for each change, or call no_changes if no footnotes exist."
    )

    return "\n".join(parts)
