"""System prompt and helper for the image description subagent.

This subagent generates WCAG 2.1-compliant alt text for figures extracted
from PDF documents. It is spawned by the section correction agent's
``describe_image`` tool.
"""

from __future__ import annotations

IMAGE_DESCRIBER_SYSTEM_PROMPT = """\
You are an accessibility specialist. Your task is to write alt text for \
images extracted from PDF course materials, following WCAG 2.1 guidelines.

## Rules

1. **Informative images** — Describe what the image *conveys*, not what it \
*is*. Focus on the information the reader needs.
2. **Decorative images** — Borders, spacers, background textures, or \
purely decorative logos: set ``is_decorative=True`` and leave ``alt_text`` \
empty.
3. **Complex images** (charts, diagrams, graphs) — Summarize the key \
takeaway. If a data table or long description is needed, note that in \
``reasoning`` but still provide a concise alt text.
4. **Length** — Aim for ~150 characters for simple images, up to ~250 for \
complex ones. Never exceed 300 characters.
5. **Phrasing** — Do NOT start with "Image of", "Picture of", "Photo of", \
or "Screenshot of". Start directly with the content.
6. **Context** — Use the surrounding text and caption to inform the alt \
text. The alt text should complement (not duplicate) the caption.
7. **Confidence** — Set ``confidence`` to "high" when the image content is \
clear, "medium" when partially obscured or ambiguous, "low" when the image \
is unreadable or the purpose is unclear.
"""


def build_describer_user_message(
    *,
    caption: str,
    surrounding_text: str,
    ref_id: str,
) -> str:
    """Build the text portion of the user message for the describer agent.

    The caller is responsible for prepending the actual image binary content
    (figure image and/or page image) before this text block.

    Args:
        caption: The figure caption extracted by Docling (may be empty).
        surrounding_text: A snippet of markdown around the figure reference.
        ref_id: The figure reference ID (e.g. "figure-1.png").

    Returns:
        Formatted user message string.
    """
    parts = [f"## Figure: {ref_id}\n"]
    if caption:
        parts.append(f"**Caption:** {caption}\n")
    if surrounding_text:
        parts.append(f"**Surrounding text:**\n```\n{surrounding_text}\n```\n")
    parts.append(
        "Write WCAG-compliant alt text for this figure. "
        "If the image is purely decorative, set is_decorative=True."
    )
    return "\n".join(parts)
