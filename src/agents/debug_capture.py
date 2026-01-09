"""Debug capture utilities for LLM call logging.

This module provides utilities for capturing full prompts and responses
from PydanticAI agent runs when debug bundle generation is requested.

Key optimization: Extracts binary image data and stores it separately,
reducing artifact size from ~2-3MB to <100KB per artifact.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .models import ImageReference

logger = logging.getLogger(__name__)

# Maximum size for an image reference (1MB limit)
MAX_IMAGE_SIZE_BYTES = 1024 * 1024


@dataclass
class ExtractedImage:
    """An image extracted from a prompt or response.

    Attributes:
        data: Raw binary image data
        media_type: MIME type (e.g., "image/png")
        ref_type: Type of image ("page", "element", "cropped")
        identifier: Unique identifier for the image
    """

    data: bytes
    media_type: str
    ref_type: str
    identifier: str


@dataclass
class PromptSerializationResult:
    """Result of serializing a prompt with image extraction.

    Attributes:
        text: Serialized prompt text with image placeholders
        images: List of extracted images
        image_references: List of ImageReference objects for the artifact
    """

    text: str
    images: list[ExtractedImage] = field(default_factory=list)
    image_references: list[ImageReference] = field(default_factory=list)


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
                    content = msg.content
                    # Handle case where content is a list of parts
                    if isinstance(content, list):
                        for part in content:
                            if hasattr(part, "content"):
                                response_parts.append(str(part.content))
                            else:
                                response_parts.append(str(part))
                    else:
                        response_parts.append(str(content))
                elif hasattr(msg, "parts"):
                    for part in msg.parts:
                        if hasattr(part, "content"):
                            response_parts.append(str(part.content))

        return "\n".join(response_parts)
    except Exception as e:
        logger.warning(f"Failed to extract raw response: {e}")
        return ""


def _extract_image_from_message(
    msg: Any, index: int, page_num: int | None = None
) -> tuple[str, ExtractedImage | None]:
    """Extract image data from a single message.

    Args:
        msg: A message that may contain image data
        index: Index of this image in the sequence
        page_num: Page number for identifier generation

    Returns:
        Tuple of (placeholder text, extracted image or None)
    """
    if not hasattr(msg, "data") or not hasattr(msg, "media_type"):
        return "", None

    try:
        data = msg.data
        media_type = getattr(msg, "media_type", "image/png")

        # Determine ref_type and identifier based on context
        if index == 0:
            ref_type = "page"
            identifier = f"page_{page_num}" if page_num else f"page_{index + 1}"
        else:
            ref_type = "element"
            identifier = f"element_{index}"

        # Check size limit
        if len(data) > MAX_IMAGE_SIZE_BYTES:
            logger.warning(
                f"Image {identifier} exceeds size limit "
                f"({len(data):,} > {MAX_IMAGE_SIZE_BYTES:,} bytes)"
            )
            return f"[IMAGE: {media_type} - TOO LARGE, SKIPPED]", None

        extracted = ExtractedImage(
            data=data,
            media_type=media_type,
            ref_type=ref_type,
            identifier=identifier,
        )

        placeholder = f"[IMAGE: {media_type} -> images/{identifier}.png]"
        return placeholder, extracted

    except Exception as e:
        logger.warning(f"Failed to extract image data: {e}")
        return "[IMAGE: extraction failed]", None


def serialize_prompt_with_images(
    messages: list[Any],
    page_num: int | None = None,
    agent_name: str = "",
) -> PromptSerializationResult:
    """Serialize a multimodal prompt, extracting images for separate storage.

    This function processes prompt messages and:
    1. Extracts binary image data for separate storage
    2. Replaces images with path reference placeholders
    3. Returns both the cleaned text and extracted images

    Args:
        messages: List of prompt messages (strings, BinaryContent, etc.)
        page_num: Page number for generating image identifiers
        agent_name: Agent name for context (e.g., "worker", "paragraph_agent")

    Returns:
        PromptSerializationResult with text, images, and references
    """
    result = PromptSerializationResult(text="")
    parts: list[str] = []
    image_index = 0

    try:
        for msg in messages:
            if isinstance(msg, str):
                parts.append(msg)
            elif hasattr(msg, "data") and hasattr(msg, "media_type"):
                # BinaryContent (image)
                placeholder, extracted = _extract_image_from_message(
                    msg, image_index, page_num
                )
                parts.append(placeholder)

                if extracted:
                    result.images.append(extracted)

                    # Create path for this image
                    path = f"images/{extracted.identifier}.png"

                    result.image_references.append(
                        ImageReference(
                            ref_type=extracted.ref_type,
                            identifier=extracted.identifier,
                            path=path,
                            media_type=extracted.media_type,
                            size_bytes=len(extracted.data),
                        )
                    )

                image_index += 1
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

        result.text = "\n".join(parts)

    except Exception as e:
        logger.warning(f"Failed to serialize prompt with images: {e}")
        result.text = str(messages)[:5000]

    return result


def serialize_prompt(messages: list[Any]) -> str:
    """Serialize a multimodal prompt to text for debug capture.

    Converts a list of prompt messages (which may include images)
    to a readable text format. Binary image content is replaced
    with [IMAGE] placeholders.

    NOTE: This is the legacy function. For new code, use
    serialize_prompt_with_images() to extract images separately.

    Args:
        messages: List of prompt messages (strings, BinaryContent, etc.)

    Returns:
        Serialized prompt text
    """
    # Use the new function but only return the text
    result = serialize_prompt_with_images(messages)
    return result.text


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


def extract_response_with_images(
    result: Any,
    page_num: int | None = None,
) -> PromptSerializationResult:
    """Extract raw response from PydanticAI result, extracting images.

    Similar to extract_raw_response but also extracts any embedded
    image data from the response messages.

    Args:
        result: PydanticAI agent result object
        page_num: Page number for generating image identifiers

    Returns:
        PromptSerializationResult with text, images, and references
    """
    extraction_result = PromptSerializationResult(text="")
    response_parts: list[str] = []
    image_index = 0

    try:
        messages = result.all_messages()

        for msg in messages:
            msg_class = msg.__class__.__name__

            # Look for model/assistant response messages
            if "Text" in msg_class or "ModelResponse" in msg_class or "Model" in msg_class:
                if hasattr(msg, "content"):
                    content = msg.content
                    # Handle case where content is a list of parts
                    if isinstance(content, list):
                        for part in content:
                            if hasattr(part, "data") and hasattr(part, "media_type"):
                                # This is an image in the response
                                placeholder, extracted = _extract_image_from_message(
                                    part, image_index, page_num
                                )
                                response_parts.append(placeholder)
                                if extracted:
                                    extraction_result.images.append(extracted)
                                    path = f"images/response_{extracted.identifier}.png"
                                    extraction_result.image_references.append(
                                        ImageReference(
                                            ref_type=extracted.ref_type,
                                            identifier=f"response_{extracted.identifier}",
                                            path=path,
                                            media_type=extracted.media_type,
                                            size_bytes=len(extracted.data),
                                        )
                                    )
                                image_index += 1
                            elif hasattr(part, "content"):
                                response_parts.append(str(part.content))
                            else:
                                part_str = str(part)
                                # Check for binary-looking content
                                if len(part_str) > 1000 and (
                                    "\\x" in part_str or "b'" in part_str[:10]
                                ):
                                    response_parts.append("[BINARY DATA]")
                                else:
                                    response_parts.append(part_str)
                    else:
                        content_str = str(content)
                        # Check for binary-looking content
                        if len(content_str) > 1000 and (
                            "\\x" in content_str or "b'" in content_str[:10]
                        ):
                            response_parts.append("[BINARY DATA]")
                        else:
                            response_parts.append(content_str)
                elif hasattr(msg, "parts"):
                    for part in msg.parts:
                        if hasattr(part, "content"):
                            response_parts.append(str(part.content))

        extraction_result.text = "\n".join(response_parts)

    except Exception as e:
        logger.warning(f"Failed to extract response with images: {e}")
        extraction_result.text = ""

    return extraction_result


def clean_binary_from_text(text: str) -> str:
    """Remove binary data patterns from serialized text.

    This function cleans up text that may contain stringified binary data
    (e.g., from repr() of bytes objects).

    Args:
        text: Text that may contain binary patterns

    Returns:
        Cleaned text with binary patterns replaced by placeholders
    """
    if not text:
        return text

    # Pattern for Python bytes repr: b'\x89PNG...'
    bytes_pattern = r"b'[^']*\\x[^']*'"

    # Pattern for raw hex sequences: \x89PNG\r\n...
    hex_pattern = r"(?:\\x[0-9a-fA-F]{2}){4,}"

    # Replace bytes patterns
    cleaned = re.sub(bytes_pattern, "[BINARY DATA]", text)

    # Replace hex sequences
    cleaned = re.sub(hex_pattern, "[BINARY DATA]", cleaned)

    # Collapse multiple consecutive placeholders
    cleaned = re.sub(r"(\[BINARY DATA\]\s*){2,}", "[BINARY DATA]\n", cleaned)

    return cleaned


__all__ = [
    "extract_raw_response",
    "serialize_prompt",
    "serialize_prompt_with_images",
    "serialize_text_prompt",
    "extract_response_with_images",
    "clean_binary_from_text",
    "ExtractedImage",
    "PromptSerializationResult",
    "MAX_IMAGE_SIZE_BYTES",
]
