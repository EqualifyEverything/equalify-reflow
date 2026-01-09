"""Prompt sanitization utilities for secure LLM prompt construction.

This module provides utilities to sanitize user-influenced data before
including it in LLM prompts, preventing prompt injection attacks.

Security Considerations:
- PDF metadata (title, author, etc.) can be crafted by attackers
- Document content may contain prompt injection markers
- All user-influenced data should be sanitized before prompt inclusion
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Threshold for logging "significant" sanitization (>20% content reduction)
SIGNIFICANT_SANITIZATION_THRESHOLD = 0.8

# Patterns that could be used for prompt injection
# These are common markers used in various LLM instruction formats
INJECTION_PATTERNS: list[str] = [
    r"</s>",  # End of sequence tokens
    r"<\|im_end\|>",  # ChatML markers
    r"<\|im_start\|>",
    r"\[INST\]",  # Llama instruction markers
    r"\[/INST\]",
    r"<<SYS>>",  # System prompt markers
    r"<</SYS>>",
    r"Human:",  # Conversation role markers
    r"Assistant:",
    r"System:",
    r"<\|user\|>",  # Additional ChatML variants
    r"<\|assistant\|>",
    r"<\|system\|>",
    r"<\|endoftext\|>",  # GPT end tokens
    r"<\|end\|>",
]

# Compiled regex for efficiency
_INJECTION_REGEX = re.compile(
    "|".join(INJECTION_PATTERNS),
    flags=re.IGNORECASE,
)


def sanitize_for_prompt(
    text: str | None,
    max_length: int = 200,
    context: str = "unknown",
) -> str:
    """Sanitize text for safe inclusion in LLM prompts.

    Removes potential prompt injection markers, escapes format string
    characters, and truncates to a safe length.

    Args:
        text: Raw text to sanitize (e.g., from PDF metadata), or None
        max_length: Maximum allowed length (default 200 chars)
        context: Field name for logging (e.g., "document_title")

    Returns:
        Sanitized text safe for prompt inclusion

    Example:
        >>> sanitize_for_prompt("Report</s>Ignore previous", context="title")
        'ReportIgnore previous'
        >>> sanitize_for_prompt("Hello {world}", context="title")
        'Hello {{world}}'
    """
    if not text:
        return ""

    original_length = len(text)
    original_text = text

    # Remove potential prompt injection markers
    text = _INJECTION_REGEX.sub("", text)

    # Escape curly braces to prevent format string issues
    # This is critical for .format() calls
    text = text.replace("{", "{{").replace("}", "}}")

    # Strip whitespace BEFORE truncation for consistent output length
    text = text.strip()

    # Truncate to max length
    if len(text) > max_length:
        text = text[:max_length] + "..."

    # Log if significant sanitization occurred (>20% reduction)
    if len(text) < original_length * SIGNIFICANT_SANITIZATION_THRESHOLD:
        logger.warning(
            f"Significant sanitization of {context}: "
            f"{original_length} -> {len(text)} chars",
            extra={
                "security_event": "prompt_sanitization",
                "field": context,
                "original_length": original_length,
                "sanitized_length": len(text),
            },
        )

    # Log if injection patterns were detected
    if _INJECTION_REGEX.search(original_text):
        logger.warning(
            f"Prompt injection markers detected in {context}",
            extra={
                "security_event": "injection_markers_detected",
                "field": context,
            },
        )

    return text


def sanitize_prompt_context(context: dict[str, Any]) -> dict[str, str]:
    """Sanitize all string values in a prompt context dictionary.

    Applies sanitize_for_prompt() to all string values in the context,
    converting non-string values to their string representation.

    Args:
        context: Dictionary of values to be formatted into prompt

    Returns:
        Dictionary with all string values sanitized

    Example:
        >>> ctx = {"title": "Report</s>", "pages": 10}
        >>> sanitize_prompt_context(ctx)
        {'title': 'Report', 'pages': '10'}
    """
    sanitized: dict[str, str] = {}

    for key, value in context.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_for_prompt(value, context=key)
        elif value is None:
            sanitized[key] = ""
        else:
            # Non-string values are safe, just convert to string
            sanitized[key] = str(value)

    return sanitized


__all__ = [
    "sanitize_for_prompt",
    "sanitize_prompt_context",
    "INJECTION_PATTERNS",
]
