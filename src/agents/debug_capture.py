"""Debug capture utilities for LLM call logging.

This module provides utilities for capturing full prompts and responses
from PydanticAI agent runs when debug bundle generation is requested.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_raw_response(result: Any) -> str:
    """Extract raw response text from PydanticAI result.

    Iterates through all messages in the result and extracts
    content from assistant/model response messages.

    Args:
        result: PydanticAI agent result object

    Returns:
        Concatenated response text from all model messages
    """
    try:
        messages = result.all_messages()
        response_parts: list[str] = []

        for msg in messages:
            msg_class = msg.__class__.__name__

            # Look for model/assistant response messages
            if "Text" in msg_class or "ModelResponse" in msg_class or "Model" in msg_class:
                if hasattr(msg, "content"):
                    response_parts.append(str(msg.content))
                elif hasattr(msg, "parts"):
                    for part in msg.parts:
                        if hasattr(part, "content"):
                            response_parts.append(str(part.content))

        return "\n".join(response_parts)
    except Exception as e:
        logger.warning(f"Failed to extract raw response: {e}")
        return ""


def serialize_prompt(messages: list[Any]) -> str:
    """Serialize a multimodal prompt to text for debug capture.

    Converts a list of prompt messages (which may include images)
    to a readable text format. Binary image content is replaced
    with [IMAGE] placeholders.

    Args:
        messages: List of prompt messages (strings, BinaryContent, etc.)

    Returns:
        Serialized prompt text
    """
    try:
        parts: list[str] = []

        for msg in messages:
            if isinstance(msg, str):
                parts.append(msg)
            elif hasattr(msg, "data") and hasattr(msg, "media_type"):
                # BinaryContent (image)
                media_type = getattr(msg, "media_type", "unknown")
                parts.append(f"[IMAGE: {media_type}]")
            elif hasattr(msg, "content"):
                parts.append(str(msg.content))
            else:
                # Try to stringify
                msg_str = str(msg)
                # Truncate very long binary-looking content
                if len(msg_str) > 1000 and ("\\x" in msg_str or "b'" in msg_str[:10]):
                    parts.append("[BINARY DATA]")
                else:
                    parts.append(msg_str)

        return "\n".join(parts)
    except Exception as e:
        logger.warning(f"Failed to serialize prompt: {e}")
        return str(messages)[:5000]


def serialize_text_prompt(prompt: str) -> str:
    """Serialize a simple text prompt.

    For agents that use plain text prompts (like page_chain),
    this just returns the prompt with minimal processing.

    Args:
        prompt: Text prompt string

    Returns:
        The prompt string (unchanged)
    """
    return prompt


__all__ = [
    "extract_raw_response",
    "serialize_prompt",
    "serialize_text_prompt",
]
