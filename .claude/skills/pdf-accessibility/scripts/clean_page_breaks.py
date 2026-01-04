#!/usr/bin/env python3
"""
Clean page break artifacts from converted PDF text.

Handles:
1. Dehyphenation - rejoining words split across lines/pages ("de-" + "veloped" → "developed")
2. Footnote extraction - moving inline footnotes to end of document
3. Page artifact removal - headers, footers, page numbers mid-text

Usage:
    python clean_page_breaks.py <markdown_path> [--output <output_path>]

Example:
    python clean_page_breaks.py /tmp/pdf-access-123/docling/document.md
"""

import re
import sys
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CleaningStats:
    """Track what was cleaned."""
    dehyphenations: int = 0
    footnotes_moved: int = 0
    page_numbers_removed: int = 0
    headers_removed: int = 0
    blank_lines_normalized: int = 0
    dehyphenated_words: list = field(default_factory=list)
    footnotes: list = field(default_factory=list)


def dehyphenate(text: str, stats: CleaningStats) -> str:
    """
    Rejoin words split by hyphens at line/page breaks.

    Patterns handled:
    - "de-\nveloped" → "developed" (hyphen + newline)
    - "de-\n\nveloped" → "developed" (hyphen + multiple newlines)
    - "de- veloping" → "developing" (hyphen + space + continuation on same line)

    Be careful not to dehyphenate intentional compound words.
    """
    # Pattern 1: lowercase letter + hyphen + newline(s) + lowercase letter
    # Catches "de-\nveloped" and "de-\n\nveloped"
    pattern_newline = r'([a-z])-\n+([a-z])'

    # Pattern 2: lowercase letter + hyphen + space + lowercase letter (mid-line breaks from PDF)
    # Catches "de- veloping" which happens when PDF extraction doesn't use newlines
    pattern_space = r'([a-z])- ([a-z])'

    def replacer(match):
        before = match.group(1)
        after = match.group(2)
        word = before + after
        stats.dehyphenations += 1
        stats.dehyphenated_words.append(f"{before}-{after} → {word}")
        return word

    text = re.sub(pattern_newline, replacer, text)
    text = re.sub(pattern_space, replacer, text)
    return text


def extract_footnotes(text: str, stats: CleaningStats) -> str:
    """
    Extract inline footnotes and move to end of document.

    Patterns:
    - Superscript numbers: ¹, ², ³ or ^1, ^2, ^3
    - Inline footnote text: "1 For a broader..." appearing mid-paragraph
    - Bracketed: [1], [2], etc.
    """
    lines = text.split('\n')
    cleaned_lines = []
    footnotes = []

    # Pattern for standalone footnote lines (number at start, followed by text)
    # e.g., "1 For a broader and more quantitative study, see Stodden [2010]"
    footnote_pattern = r'^(\d+)\s+(.+)$'

    # Pattern for lines that look like page breaks followed by footnotes
    # (short numeric line or blank, then footnote)

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check if this looks like a footnote line
        match = re.match(footnote_pattern, line)
        if match:
            num = match.group(1)
            content = match.group(2)

            # Heuristic: if it starts with a capital and looks like a sentence,
            # and the number is small (1-20), it's probably a footnote
            if int(num) <= 20 and content and content[0].isupper():
                footnotes.append((num, content))
                stats.footnotes_moved += 1
                stats.footnotes.append(f"[{num}] {content[:50]}...")
                i += 1
                continue

        cleaned_lines.append(lines[i])
        i += 1

    result = '\n'.join(cleaned_lines)

    # Add footnotes at end if any were extracted
    if footnotes:
        result += '\n\n---\n\n## Footnotes\n\n'
        for num, content in sorted(footnotes, key=lambda x: int(x[0])):
            result += f'{num}. {content}\n\n'

    return result


def remove_page_artifacts(text: str, stats: CleaningStats) -> str:
    """
    Remove page numbers, headers, and footers that appear mid-text.

    Common patterns:
    - Standalone numbers (page numbers): "42" on its own line
    - Running headers: repeated document title
    - "Page X of Y" patterns
    """
    lines = text.split('\n')
    cleaned_lines = []

    for line in lines:
        stripped = line.strip()

        # Skip standalone page numbers (1-4 digits, nothing else)
        if re.match(r'^\d{1,4}$', stripped):
            stats.page_numbers_removed += 1
            continue

        # Skip "Page X" or "Page X of Y" patterns
        if re.match(r'^[Pp]age\s+\d+(\s+of\s+\d+)?$', stripped):
            stats.page_numbers_removed += 1
            continue

        # Skip lines that are just dashes or underscores (page separators)
        if re.match(r'^[-_=]{3,}$', stripped):
            continue

        cleaned_lines.append(line)

    return '\n'.join(cleaned_lines)


def normalize_whitespace(text: str, stats: CleaningStats) -> str:
    """
    Normalize excessive blank lines while preserving paragraph structure.

    - Multiple blank lines → two blank lines (paragraph break)
    - Single blank line preserved
    """
    # Replace 3+ consecutive newlines with 2 (paragraph break)
    original_len = len(text)
    result = re.sub(r'\n{3,}', '\n\n', text)

    if len(result) < original_len:
        stats.blank_lines_normalized += 1

    return result


def join_broken_sentences(text: str, stats: CleaningStats) -> str:
    """
    Join sentences that were broken across pages.

    Heuristic: If a line ends without sentence-ending punctuation
    and the next line starts with a lowercase letter, join them.
    """
    lines = text.split('\n')
    result_lines = []

    i = 0
    while i < len(lines):
        current = lines[i]

        # Check if this line might continue on the next
        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            current_stripped = current.rstrip()

            # If current line ends mid-sentence and next starts lowercase
            if (current_stripped and
                not current_stripped[-1] in '.!?:"\')' and
                next_line and
                next_line[0].islower()):
                # Join with space
                result_lines.append(current.rstrip() + ' ' + next_line)
                i += 2
                continue

        result_lines.append(current)
        i += 1

    return '\n'.join(result_lines)


def clean_page_breaks(text: str) -> tuple[str, CleaningStats]:
    """
    Apply all page break cleaning operations.

    Returns:
        Tuple of (cleaned_text, stats)
    """
    stats = CleaningStats()

    # Order matters - dehyphenate before joining sentences
    text = dehyphenate(text, stats)
    text = extract_footnotes(text, stats)
    text = remove_page_artifacts(text, stats)
    text = join_broken_sentences(text, stats)
    text = normalize_whitespace(text, stats)

    return text, stats


def main():
    parser = argparse.ArgumentParser(
        description='Clean page break artifacts from converted PDF text'
    )
    parser.add_argument('markdown_path', help='Path to markdown file')
    parser.add_argument('--output', '-o', help='Output path (default: overwrite input)')
    parser.add_argument('--dry-run', '-n', action='store_true',
                        help='Show what would be changed without modifying')

    args = parser.parse_args()

    input_path = Path(args.markdown_path)
    if not input_path.exists():
        print(f"Error: File not found: {input_path}")
        sys.exit(1)

    with open(input_path) as f:
        original = f.read()

    cleaned, stats = clean_page_breaks(original)

    # Report
    print("=== Page Break Cleaning Report ===")
    print(f"  Dehyphenations: {stats.dehyphenations}")
    if stats.dehyphenated_words:
        for word in stats.dehyphenated_words[:5]:
            print(f"    - {word}")
        if len(stats.dehyphenated_words) > 5:
            print(f"    ... and {len(stats.dehyphenated_words) - 5} more")

    print(f"  Footnotes moved: {stats.footnotes_moved}")
    if stats.footnotes:
        for fn in stats.footnotes[:3]:
            print(f"    - {fn}")

    print(f"  Page numbers removed: {stats.page_numbers_removed}")
    print(f"  Whitespace normalized: {'yes' if stats.blank_lines_normalized else 'no'}")

    if args.dry_run:
        print("\n[Dry run - no changes written]")
        # Show diff preview
        if original != cleaned:
            print("\nFirst 500 chars of cleaned output:")
            print("-" * 40)
            print(cleaned[:500])
    else:
        output_path = Path(args.output) if args.output else input_path
        with open(output_path, 'w') as f:
            f.write(cleaned)
        print(f"\nWritten to: {output_path}")


if __name__ == "__main__":
    main()
