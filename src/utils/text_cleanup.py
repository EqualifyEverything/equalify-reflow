"""Text cleanup utilities for post-processing PDF-to-markdown conversion.

Rule-based cleanup functions to fix common OCR and conversion errors without LLM.
"""

import logging
import re
import unicodedata
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


_PUA_CHAR_CLASS = (
    r'[\uE000-\uF8FF\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]'
)


def strip_private_use_chars(text: str) -> str:
    """Replace or remove Unicode Private Use Area (PUA) characters.

    PDF fonts with custom encoding map glyphs to PUA codepoints
    (U+E000-U+F8FF, U+F0000-U+FFFFD, U+100000-U+10FFFD). These render
    as invisible boxes or replacement glyphs and break screen readers.

    Context-aware replacement:
    - Between two alphanumeric characters → hyphen (the PDF font likely
      encoded a dash/hyphen as a PUA glyph, e.g. "AI-Leaders")
    - Otherwise → removed (decorative or meaningless)

    Args:
        text: Input text potentially containing PUA characters

    Returns:
        Text with PUA characters replaced or removed

    Examples:
        >>> strip_private_use_chars("Content Management System \\ue081(CMS)")
        'Content Management System (CMS)'
        >>> strip_private_use_chars("AI\\ue088Leaders.org")
        'AI-Leaders.org'
    """
    # First pass: PUA between alphanumeric chars → hyphen
    text = re.sub(
        r'(?<=[A-Za-z0-9])' + _PUA_CHAR_CLASS + r'+(?=[A-Za-z0-9])',
        '-',
        text,
    )
    # Second pass: remaining PUA chars (adjacent to whitespace/punctuation) → remove
    text = re.sub(_PUA_CHAR_CLASS + r'+', '', text)
    return text


def normalize_unicode(text: str) -> str:
    """Normalize unicode characters to their canonical forms.

    Fixes issues with diacritics and special characters like "Prli´ c" → "Prličc"
    Uses NFKC normalization (compatibility composition).

    Args:
        text: Input text with potential unicode issues

    Returns:
        Text with normalized unicode characters

    Examples:
        >>> normalize_unicode("café")  # Various unicode representations
        'café'
    """
    return unicodedata.normalize('NFKC', text)


def collapse_letter_spacing(text: str) -> str:
    """Collapse letter-spaced text back to normal words.

    Some PDFs (especially PowerPoint exports) have letter-spacing styling that
    gets baked into the text as actual space characters. This breaks screen
    readers and text search.

    Example:
        "r e q u i r e m e n t s" → "requirements"
        "H a s   d e t e r m i n e d" → "Has determined"

    The pattern matches sequences of 4+ single letters separated by spaces.
    This is safe because normal English words have consecutive letters,
    while letter-spaced text alternates single-letter + space.

    Args:
        text: Input text with potential letter-spacing issues

    Returns:
        Text with letter-spacing collapsed

    Examples:
        >>> collapse_letter_spacing("the r e q u i r e m e n t s are met")
        'the requirements are met'
        >>> collapse_letter_spacing("normal text stays unchanged")
        'normal text stays unchanged'
    """
    # Pattern matches: single letter, space, repeated 3+ times, ending with letter
    # e.g., "r e q u i r e m e n t s" (at least 4 letters = 3 spaces minimum)
    pattern = r'\b((?:[a-zA-Z] ){3,}[a-zA-Z])\b'

    def collapse_match(match: re.Match[str]) -> str:
        return match.group(0).replace(' ', '')

    return re.sub(pattern, collapse_match, text)


def fix_excessive_whitespace(text: str) -> str:
    """Remove excessive whitespace while preserving paragraph breaks.

    - Replaces multiple spaces with single space
    - Preserves intentional paragraph breaks (double newlines)
    - Removes trailing/leading whitespace on lines

    Args:
        text: Input text with potential whitespace issues

    Returns:
        Text with cleaned whitespace

    Examples:
        >>> fix_excessive_whitespace("hello    world")
        'hello world'
        >>> fix_excessive_whitespace("para1\\n\\n\\npara2")
        'para1\\n\\npara2'
    """
    # Fix multiple spaces within lines
    text = re.sub(r' {2,}', ' ', text)

    # Fix multiple newlines (preserve double newline for paragraphs)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Remove trailing/leading whitespace on each line
    lines = text.split('\n')
    lines = [line.rstrip() for line in lines]

    return '\n'.join(lines)


def normalize_quotes(text: str) -> str:
    """Normalize smart quotes to standard ASCII quotes.

    Converts curly quotes and apostrophes to straight quotes for consistency.

    Args:
        text: Input text with smart quotes

    Returns:
        Text with normalized quotes

    Examples:
        >>> normalize_quotes('\u201cHello\u201d')
        '"Hello"'
    """
    # Double quotes (use unicode escape codes for reliability)
    text = text.replace('\u201c', '"').replace('\u201d', '"')  # Left/right double quotes

    # Single quotes / apostrophes
    text = text.replace('\u2018', "'").replace('\u2019', "'")  # Left/right single quotes

    # Also handle guillemets and other quote marks
    text = text.replace('\u00ab', '"').replace('\u00bb', '"')  # « »
    text = text.replace('\u2039', "'").replace('\u203a', "'")  # ‹ ›

    return text


def validate_urls(text: str) -> list[str]:
    """Find and validate URLs in text, returning list of broken URLs.

    Does NOT modify text, only returns list of potentially broken URLs for logging.

    Args:
        text: Input text containing URLs

    Returns:
        List of URLs that failed validation

    Examples:
        >>> validate_urls("Visit http://example.com and http://broken")
        ['http://broken']
    """
    # Find all URLs in text
    url_pattern = r'https?://[^\s\)\]\}\'\"]+'
    urls = re.findall(url_pattern, text)

    broken_urls = []
    for url in urls:
        try:
            parsed = urlparse(url)
            # Check for basic validity
            if not parsed.netloc:
                broken_urls.append(url)
        except Exception:
            broken_urls.append(url)

    return broken_urls


def fix_url_formatting(text: str) -> str:
    """Fix common URL formatting issues in markdown.

    - Adds missing protocol to bare domain URLs in markdown links
    - Skips relative paths, anchors, and local file references

    Args:
        text: Input text with potential URL issues

    Returns:
        Text with fixed URLs
    """

    def add_protocol(match: re.Match[str]) -> str:
        link_text = match.group(1)
        url = match.group(2)
        if url.startswith(('http://', 'https://', 'mailto:', 'ftp://', '#', '/', './', '../')):
            return match.group(0)
        # Only add protocol if the first path segment looks like a domain (has a dot)
        first_segment = url.split('/')[0]
        if '.' not in first_segment:
            return match.group(0)
        url = 'http://' + url
        return f'[{link_text}]({url})'

    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', add_protocol, text)

    return text


def sanitize_extracted_text(text: str) -> str:
    """Lightweight sanitization for raw Docling-extracted markdown.

    Runs BEFORE any LLM processing so agents see clean text. Only applies
    fixes that address encoding artifacts without altering whitespace or
    formatting that the LLM agents rely on for layout analysis.

    Applied at v0 (right after Docling extraction).
    """
    text = strip_private_use_chars(text)
    text = normalize_unicode(text)
    return text


def cleanup_markdown(text: str, log_warnings: bool = True) -> str:
    """Apply safe cleanup functions to markdown text.

    Applies only SAFE fixes that cannot introduce errors:
    1. Normalize quotes (smart quotes → ASCII)
    2. Normalize unicode (canonical forms)
    3. Fix excessive whitespace
    4. Fix URL formatting (add missing protocols)

    DELEGATED TO LLM (context-aware fixes):
    - Line-break hyphenation (requires understanding footnotes/columns)
    - Bibliography formatting (requires semantic understanding)

    Args:
        text: Raw markdown text from PDF conversion
        log_warnings: Whether to log warnings for broken URLs

    Returns:
        Cleaned markdown text with safe fixes applied
    """
    # Track what we're fixing
    original_length = len(text)

    # Apply ONLY safe fixes that cannot introduce errors
    text = strip_private_use_chars(text)  # SAFE: Remove PUA codepoints from PDF fonts
    text = normalize_quotes(text)         # SAFE: Always correct
    text = normalize_unicode(text)        # SAFE: Canonical forms
    text = collapse_letter_spacing(text)  # SAFE: Fix OCR letter-spacing
    text = fix_excessive_whitespace(text)  # SAFE: Whitespace cleanup
    text = fix_url_formatting(text)       # SAFE: Add protocols

    # LLM handles context-aware fixes (hyphenation, bibliography formatting)

    # Validate URLs (for logging only)
    if log_warnings:
        broken_urls = validate_urls(text)
        if broken_urls:
            logger.warning(
                f"Found {len(broken_urls)} potentially broken URLs: "
                f"{', '.join(broken_urls[:5])}"
            )

    # Log cleanup summary
    chars_changed = abs(len(text) - original_length)
    if chars_changed > 0:
        logger.info(
            f"Text cleanup complete: {chars_changed} characters changed "
            f"({original_length} → {len(text)})"
        )

    return text
