"""V5 Pipeline Validation Gate.

The validation gate checks all proposed edits before they are committed.
This catches agent mistakes and enforces quality standards.

Validation checks:
    1. Spell check - using document dictionary
    2. Markdown lint - valid formatting
    3. Consistency check - target exists, edit is reasonable
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .models import EditProposal, SpellIssue, TaskType, ValidationResult

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Spell Checking
# =============================================================================


def _check_spelling(
    text: str,
    dictionary: list[str],
) -> list[SpellIssue]:
    """Check text for spelling issues.

    Uses pyspellchecker with document-specific dictionary additions.

    Args:
        text: Text to check
        dictionary: Document-specific terms to accept

    Returns:
        List of spelling issues found
    """
    try:
        from spellchecker import SpellChecker

        spell = SpellChecker()
    except ImportError:
        logger.warning("spellchecker not installed, skipping spell check")
        return []

    # Build case-insensitive dictionary lookup
    dict_lower = {term.lower(): term for term in dictionary}

    # Extract words (3+ chars to avoid false positives on short words)
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text)

    issues: list[SpellIssue] = []
    seen: set[str] = set()

    for word in words:
        word_lower = word.lower()

        # Skip if already checked or in document dictionary
        if word_lower in seen or word_lower in dict_lower:
            continue

        seen.add(word_lower)

        # Check if in standard dictionary
        if word_lower not in spell:
            # Get suggestion
            suggestion = spell.correction(word_lower) or ""

            # Preserve original case in suggestion
            if suggestion and word[0].isupper():
                suggestion = suggestion.capitalize()

            issues.append(
                SpellIssue(
                    word=word,
                    suggestion=suggestion,
                    in_dictionary=False,
                )
            )

    return issues


# =============================================================================
# Markdown Linting
# =============================================================================


def _check_markdown(text: str) -> list[str]:
    """Check markdown for formatting issues.

    Args:
        text: Markdown text to check

    Returns:
        List of issue descriptions
    """
    issues: list[str] = []
    lines = text.split("\n")

    for i, line in enumerate(lines, 1):
        # Check for heading without space after #
        if re.match(r"^#+[^#\s]", line):
            issues.append(f"Line {i}: heading missing space after #")

        # Check for unclosed brackets
        if line.count("[") != line.count("]"):
            issues.append(f"Line {i}: unclosed square brackets")

        if line.count("(") != line.count(")"):
            # Skip if it's alt-text with parentheses in content
            if not re.search(r"!\[.*\]\(", line):
                issues.append(f"Line {i}: unclosed parentheses")

        # Check for trailing whitespace (minor)
        if line.rstrip() != line and line.strip():
            issues.append(f"Line {i}: trailing whitespace")

        # Check for broken image syntax
        if re.search(r"!\[[^\]]*$", line):
            issues.append(f"Line {i}: incomplete image syntax")

        # Check for broken table syntax (pipe at start but not at end)
        if line.startswith("|") and not line.rstrip().endswith("|"):
            issues.append(f"Line {i}: table row missing closing pipe")

    # Check for excessive consecutive blank lines
    blank_count = 0
    for i, line in enumerate(lines, 1):
        if not line.strip():
            blank_count += 1
            if blank_count > 2:
                issues.append(f"Line {i}: excessive blank lines")
        else:
            blank_count = 0

    return issues


# =============================================================================
# Consistency Checking
# =============================================================================


def _check_consistency(
    proposal: EditProposal,
    current_markdown: str,
) -> list[str]:
    """Check edit for consistency issues.

    Args:
        proposal: The proposed edit
        current_markdown: Current markdown content

    Returns:
        List of issue descriptions
    """
    issues: list[str] = []

    # Check if target text exists
    if proposal.before and proposal.before not in current_markdown:
        # Provide helpful feedback about what went wrong
        preview = proposal.before[:50] + "..." if len(proposal.before) > 50 else proposal.before
        issues.append(
            f"Target text not found in markdown. Your 'before' text was: '{preview}'. "
            "Use find_text() to get the EXACT text from the markdown, then copy the exact_match value."
        )

    # Check if replacement is different
    if proposal.before == proposal.after:
        issues.append("Replacement is identical to original")

    # Check for unreasonably large edits
    if len(proposal.after) > 10000:
        issues.append("Edit is very large (>10000 chars). Break into smaller edits.")

    # Check for empty replacement where content expected
    if proposal.task_type in (TaskType.ALT_TEXT, TaskType.TABLE_TRANSCRIPTION):
        if not proposal.after.strip():
            issues.append(f"Empty content for {proposal.task_type.value}")

    # Validate alt-text format
    if proposal.task_type == TaskType.ALT_TEXT:
        # Should produce valid markdown image
        if not re.search(r"!\[.+\]\(.+\)", proposal.after):
            # Unless it's marking as decorative
            if not re.search(r"!\[\]\(.+\)", proposal.after):
                issues.append("Alt-text edit doesn't produce valid image markdown")

    # Validate table format
    if proposal.task_type == TaskType.TABLE_TRANSCRIPTION:
        if "|" not in proposal.after:
            issues.append("Table transcription doesn't contain pipe characters")

    return issues


# =============================================================================
# Main Validation Function
# =============================================================================


def validate_edit(
    proposal: EditProposal,
    dictionary: list[str],
    current_markdown: str,
    strict: bool = False,
) -> ValidationResult:
    """Validate a proposed edit before committing.

    This is the main validation gate that all edits must pass through.

    Args:
        proposal: The proposed edit from a worker
        dictionary: Document-specific terms for spell-checking
        current_markdown: Current markdown content
        strict: If True, reject on any issue. If False, only reject on serious issues.

    Returns:
        ValidationResult with approval status and feedback
    """
    logger.debug(f"Validating edit for target: {proposal.target}")

    # Collect all issues
    spell_issues = _check_spelling(proposal.after, dictionary)
    lint_issues = _check_markdown(proposal.after)
    consistency_issues = _check_consistency(proposal, current_markdown)

    # Determine if we should reject
    # In strict mode, any issue causes rejection
    # In normal mode, only consistency issues cause rejection
    serious_issues = []

    if consistency_issues:
        serious_issues.extend(consistency_issues)

    # Spell issues with suggestions are likely real typos
    typo_issues = [s for s in spell_issues if s.suggestion and len(s.suggestion) > 2]
    if strict and typo_issues:
        serious_issues.append(
            f"Possible typos: {[f'{s.word} -> {s.suggestion}' for s in typo_issues[:3]]}"
        )

    # Serious lint issues (not just trailing whitespace)
    serious_lint = [i for i in lint_issues if "whitespace" not in i.lower()]
    if serious_lint:
        serious_issues.extend(serious_lint[:3])  # Cap at 3 issues

    # Build feedback message
    if serious_issues:
        feedback = "Validation failed:\n" + "\n".join(f"- {i}" for i in serious_issues)

        logger.warning(f"Edit rejected for {proposal.target}: {feedback}")

        return ValidationResult(
            approved=False,
            edit=proposal,
            spell_issues=spell_issues,
            lint_issues=lint_issues,
            consistency_issues=consistency_issues,
            feedback=feedback,
        )

    # Approved!
    logger.debug(f"Edit approved for {proposal.target}")

    return ValidationResult(
        approved=True,
        edit=proposal,
        spell_issues=spell_issues,  # Include for reference even if approved
        lint_issues=lint_issues,
        consistency_issues=[],
    )


def auto_fix_minor_issues(text: str) -> str:
    """Auto-fix minor formatting issues that don't need agent revision.

    Args:
        text: Text to fix

    Returns:
        Fixed text
    """
    # Remove trailing whitespace
    lines = text.split("\n")
    lines = [line.rstrip() for line in lines]

    # Remove excessive blank lines (more than 2)
    result = []
    blank_count = 0
    for line in lines:
        if not line:
            blank_count += 1
            if blank_count <= 2:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)

    return "\n".join(result)
