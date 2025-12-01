"""Markdown cleanup utilities using mdformat and pyspellchecker.

This module cleans up Docling markdown output before LLM processing.

Auto-fixes (via mdformat):
- Removes trailing whitespace
- Collapses multiple blank lines to one
- Ensures file ends with single newline
- Normalizes list markers and code block formatting

Spell checking (via pyspellchecker):
- Flags potential misspellings for LLM to verify against page image
- Skips code blocks, inline code, and URLs
- Uses technical dictionary (config/technical_dictionary.txt) to avoid
  flagging domain-specific terms
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import mdformat
from spellchecker import SpellChecker

logger = logging.getLogger(__name__)

# Path to technical dictionary file
TECHNICAL_DICT_PATH = Path("config/technical_dictionary.txt")


@dataclass
class SpellingFlag:
    """A flagged potential misspelling for LLM review."""

    word: str
    suggestions: list[str]
    line_number: int
    context: str


@dataclass
class MarkdownCleanupResult:
    """Result of markdown cleanup processing."""

    cleaned_text: str
    formatting_changes: int  # Number of characters changed by mdformat
    spelling_flags: list[SpellingFlag]
    words_checked: int
    words_flagged: int


def _load_technical_dictionary() -> set[str]:
    """Load technical terms that should not be flagged as misspellings.

    Returns:
        Set of technical terms (lowercase)
    """
    if not TECHNICAL_DICT_PATH.exists():
        logger.warning(
            f"Technical dictionary not found at {TECHNICAL_DICT_PATH}, "
            "using empty dictionary"
        )
        return set()

    try:
        with open(TECHNICAL_DICT_PATH) as f:
            terms = set()
            for line in f:
                # Skip comments and empty lines
                line = line.strip()
                if line and not line.startswith("#"):
                    terms.add(line.lower())
            logger.debug(f"Loaded {len(terms)} technical terms from dictionary")
            return terms
    except Exception as e:
        logger.error(f"Error loading technical dictionary: {e}")
        return set()


def _extract_prose_words(text: str) -> list[tuple[str, int, str]]:
    """Extract words from prose sections, skipping code and URLs.

    Args:
        text: Markdown text

    Returns:
        List of (word, line_number, context) tuples
    """
    words = []
    lines = text.split("\n")

    in_code_block = False

    for line_num, line in enumerate(lines, 1):
        # Track fenced code blocks
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            continue

        if in_code_block:
            continue

        # Skip indented code blocks (4 spaces or tab at start of line)
        if line.startswith(("    ", "\t")):
            continue

        # Remove inline code spans
        line_clean = re.sub(r"`[^`]+`", "", line)

        # Remove URLs
        line_clean = re.sub(r"https?://[^\s\)\]]+", "", line_clean)
        line_clean = re.sub(r"<[^>]+>", "", line_clean)  # Email/URL in brackets

        # Remove markdown link URLs but keep link text
        line_clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", line_clean)

        # Remove image syntax
        line_clean = re.sub(r"!\[([^\]]*)\]\([^\)]+\)", "", line_clean)

        # Remove markdown formatting characters
        line_clean = re.sub(r"[#*_\[\](){}|>~]", " ", line_clean)

        # Extract words (letters only, min 2 chars)
        word_matches = re.findall(r"\b[a-zA-Z]{2,}\b", line_clean)

        for word in word_matches:
            # Get context (surrounding text)
            context = line.strip()[:80]
            words.append((word, line_num, context))

    return words


def _create_spell_checker(technical_terms: set[str]) -> SpellChecker:
    """Create and configure spell checker with technical dictionary.

    Args:
        technical_terms: Set of technical terms to add to dictionary

    Returns:
        Configured SpellChecker instance
    """
    spell = SpellChecker(language="en", distance=1)

    # Add technical terms to the dictionary
    if technical_terms:
        spell.word_frequency.load_words(list(technical_terms))

    return spell


def format_markdown(text: str) -> tuple[str, int]:
    """Apply mdformat to fix markdown formatting issues.

    Args:
        text: Raw markdown text

    Returns:
        Tuple of (formatted_text, characters_changed)
    """
    original_len = len(text)

    try:
        # mdformat.text() returns formatted markdown
        # Extensions like mdformat-gfm are auto-detected if installed
        formatted = mdformat.text(text)

        chars_changed = abs(len(formatted) - original_len)

        if chars_changed > 0:
            logger.info(
                f"mdformat applied: {chars_changed} characters changed "
                f"({original_len} -> {len(formatted)})"
            )

        return formatted, chars_changed

    except Exception as e:
        logger.warning(f"mdformat failed, returning original text: {e}")
        return text, 0


def check_spelling(
    text: str,
    technical_terms: set[str] | None = None,
    max_flags: int = 50,
) -> tuple[list[SpellingFlag], int, int]:
    """Check spelling and return flagged words for LLM review.

    Does NOT auto-correct - only flags potential issues for the LLM
    to verify against the visual page image.

    Args:
        text: Markdown text to check
        technical_terms: Optional set of technical terms to ignore
        max_flags: Maximum number of flags to return (avoid overwhelming LLM)

    Returns:
        Tuple of (spelling_flags, words_checked, words_flagged)
    """
    if technical_terms is None:
        technical_terms = _load_technical_dictionary()

    spell = _create_spell_checker(technical_terms)

    # Extract prose words with line numbers and context
    word_data = _extract_prose_words(text)
    words_checked = len(word_data)

    # Find misspelled words
    unique_words = {w.lower() for w, _, _ in word_data}
    misspelled = spell.unknown(unique_words)

    # Filter out likely proper nouns (capitalized words)
    # and very short words that are often acronyms
    flags: list[SpellingFlag] = []
    seen_words: set[str] = set()

    for word, line_num, context in word_data:
        word_lower = word.lower()

        # Skip if already flagged or not misspelled
        if word_lower in seen_words or word_lower not in misspelled:
            continue

        # Skip likely proper nouns (first letter capitalized, not start of sentence)
        # This is a heuristic - LLM will verify against image
        if word[0].isupper() and not context.startswith(word):
            continue

        # Skip very short words (likely acronyms)
        if len(word) < 3:
            continue

        # Get suggestions
        suggestions = list(spell.candidates(word_lower) or [])[:3]

        # Only flag if we have suggestions (otherwise likely a valid rare word)
        if suggestions:
            flags.append(
                SpellingFlag(
                    word=word,
                    suggestions=suggestions,
                    line_number=line_num,
                    context=context,
                )
            )
            seen_words.add(word_lower)

        if len(flags) >= max_flags:
            logger.info(f"Reached max spelling flags ({max_flags}), stopping")
            break

    logger.info(
        f"Spell check complete: {words_checked} words checked, "
        f"{len(flags)} potential misspellings flagged"
    )

    return flags, words_checked, len(flags)


def cleanup_markdown(
    text: str,
    enable_formatting: bool = True,
    enable_spell_check: bool = True,
    technical_terms: set[str] | None = None,
) -> MarkdownCleanupResult:
    """Apply markdown formatting fixes and spell checking.

    This is the main entry point for the cleanup pipeline.

    Args:
        text: Raw markdown text from Docling
        enable_formatting: Whether to apply mdformat fixes
        enable_spell_check: Whether to check spelling
        technical_terms: Optional technical dictionary (loads from file if None)

    Returns:
        MarkdownCleanupResult with cleaned text and flags
    """
    cleaned_text = text
    formatting_changes = 0
    spelling_flags: list[SpellingFlag] = []
    words_checked = 0
    words_flagged = 0

    # Step 1: Apply mdformat for deterministic fixes
    if enable_formatting:
        cleaned_text, formatting_changes = format_markdown(text)

    # Step 2: Check spelling on the formatted text
    if enable_spell_check:
        if technical_terms is None:
            technical_terms = _load_technical_dictionary()

        spelling_flags, words_checked, words_flagged = check_spelling(
            cleaned_text, technical_terms
        )

    return MarkdownCleanupResult(
        cleaned_text=cleaned_text,
        formatting_changes=formatting_changes,
        spelling_flags=spelling_flags,
        words_checked=words_checked,
        words_flagged=words_flagged,
    )


def format_spelling_flags_for_prompt(flags: list[SpellingFlag]) -> str:
    """Format spelling flags as text for inclusion in LLM prompt.

    Args:
        flags: List of SpellingFlag objects

    Returns:
        Formatted string for prompt, or empty string if no flags
    """
    if not flags:
        return ""

    lines = ["**Flagged potential misspellings** (verify against page image):"]

    for flag in flags:
        suggestions_str = ", ".join(f'"{s}"' for s in flag.suggestions)
        lines.append(
            f"- Line {flag.line_number}: \"{flag.word}\" - "
            f"suggestions: [{suggestions_str}]"
        )

    return "\n".join(lines)
