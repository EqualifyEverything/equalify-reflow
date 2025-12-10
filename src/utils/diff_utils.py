"""Utilities for search-replace diff operations.

This module provides validation and helper functions for working with
search-replace diffs in the remediation pipeline. It ensures diffs are
safe to apply and helps generate minimal unique search strings.
"""


def validate_search_replace(
    markdown: str,
    search: str,
    replace: str
) -> tuple[bool, str | None]:
    """Validate a search-replace operation.

    Checks that the search text can be safely applied:
    - Search text is not empty
    - Search and replace are different
    - Search text exists exactly once in document

    Args:
        markdown: Current markdown content
        search: Text to find
        replace: Text to substitute

    Returns:
        Tuple of (is_valid, error_message)
        - (True, None) if valid
        - (False, "error description") if invalid

    Example:
        >>> valid, err = validate_search_replace("# Hello", "# Hello", "# Hi")
        >>> valid
        True
        >>> valid, err = validate_search_replace("# Hello", "World", "Hi")
        >>> valid
        False
        >>> err
        'Search text not found in document'
    """
    if not search:
        return False, "Search text cannot be empty"

    if search == replace:
        return False, "Search and replace text are identical"

    # Check uniqueness
    count = markdown.count(search)
    if count == 0:
        return False, "Search text not found in document"
    if count > 1:
        return False, f"Search text matches {count} locations (must be unique)"

    return True, None


def find_unique_context(
    markdown: str,
    target: str,
    min_context: int = 20,
    max_context: int = 100
) -> str | None:
    """Find minimal unique context around target text.

    Expands context around the target text until the resulting string
    is unique in the document. Useful for generating search strings
    that won't match multiple locations.

    Args:
        markdown: Document content
        target: Text to make unique
        min_context: Minimum characters of context to add
        max_context: Maximum characters of context to try

    Returns:
        Unique search string containing target, or None if:
        - target not found in document
        - could not make unique within max_context

    Example:
        >>> doc = "The cat sat. The cat ran."
        >>> find_unique_context(doc, "cat", min_context=5, max_context=20)
        'The cat sat'  # or similar unique context
    """
    if target not in markdown:
        return None

    # Already unique
    if markdown.count(target) == 1:
        return target

    # Find first occurrence
    idx = markdown.find(target)

    # Expand context until unique
    for ctx in range(min_context, max_context + 1, 10):
        start = max(0, idx - ctx)
        end = min(len(markdown), idx + len(target) + ctx)
        candidate = markdown[start:end]

        if markdown.count(candidate) == 1:
            return candidate

    return None


def apply_diff(markdown: str, search: str, replace: str) -> str | None:
    """Apply a search-replace diff to markdown.

    Validates the diff is safe to apply, then performs the replacement.

    Args:
        markdown: Current markdown content
        search: Text to find
        replace: Text to substitute

    Returns:
        Modified markdown if successful, None if validation failed

    Example:
        >>> apply_diff("# Hello World", "Hello", "Hi")
        '# Hi World'
        >>> apply_diff("# Hello Hello", "Hello", "Hi")  # Not unique
        None
    """
    valid, _ = validate_search_replace(markdown, search, replace)
    if not valid:
        return None

    return markdown.replace(search, replace, 1)


def count_diff_changes(
    original: str,
    search: str,
    replace: str
) -> dict[str, int]:
    """Count the character-level changes a diff would make.

    Useful for estimating the impact of a proposed change.

    Args:
        original: Original markdown content
        search: Text to find
        replace: Text to substitute

    Returns:
        Dict with keys:
        - chars_removed: Characters being removed
        - chars_added: Characters being added
        - net_change: Net character change (added - removed)

    Example:
        >>> count_diff_changes("# Hello", "Hello", "Hello World")
        {'chars_removed': 5, 'chars_added': 11, 'net_change': 6}
    """
    chars_removed = len(search)
    chars_added = len(replace)

    return {
        "chars_removed": chars_removed,
        "chars_added": chars_added,
        "net_change": chars_added - chars_removed,
    }


__all__ = [
    "validate_search_replace",
    "find_unique_context",
    "apply_diff",
    "count_diff_changes",
]
