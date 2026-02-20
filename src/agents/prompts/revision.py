"""System prompts and user message builders for the revision / feedback agents.

Two agents live here:

1. **Decomposition agent** — Takes freeform feedback and produces a
   ``TaskDecompositionResult`` (structured output, Haiku).
2. **Revision agent** — Applies discrete revision tasks to a single page
   via ``str_replace`` / ``no_changes`` tools (Haiku).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...services.pipeline_viewer_models import PageAttributes, RevisionTask
    from ..prompts import revision as _self_module  # noqa: F401

# ---------------------------------------------------------------------------
# Decomposition agent
# ---------------------------------------------------------------------------

DECOMPOSITION_SYSTEM_PROMPT = """\
You are a feedback decomposition agent for a PDF-to-accessible-markdown \
pipeline. You receive freeform reviewer feedback about the converted \
document and must break it into discrete, actionable revision tasks.

## Output format

Return a JSON object with two keys:
- **tasks** — an ordered list of revision task objects, each with:
  - `id` (int): sequential 1-based identifier
  - `description` (str): clear, specific instruction for the revision agent
  - `affected_pages` (list[int]): page numbers this task applies to
  - `needs_image` (bool): true only if the revision agent needs the page \
image (e.g. verifying visual layout, checking image presence)
  - `category` (str): one of "content", "formatting", "accessibility", \
"structure"
- **reasoning** — a brief explanation of how you decomposed the feedback.

## Guidelines

- Be specific. "Fix the heading on page 3" is better than "Fix headings".
- If the feedback mentions specific pages, use those. Otherwise infer from \
the document outline which pages are affected.
- Keep tasks atomic — one logical change per task.
- Set `needs_image` conservatively. Most text corrections do NOT need the \
page image.
- If prior feedback rounds are provided, avoid re-creating tasks that were \
already addressed.
"""


def build_decomposition_user_message(
    feedback: str,
    outline_text: str,
    total_pages: int,
    page_attributes_summary: str,
    feedback_history: list[str] | None = None,
) -> str:
    """Build the user message for the decomposition agent.

    Args:
        feedback: Current round of freeform reviewer feedback.
        outline_text: Pre-formatted document outline string.
        total_pages: Total page count of the document.
        page_attributes_summary: Pre-formatted page attributes summary.
        feedback_history: Previous rounds of feedback (oldest first).

    Returns:
        Markdown-formatted user message.
    """
    parts: list[str] = []

    parts.append("## Reviewer Feedback\n")
    parts.append(feedback)
    parts.append("")

    if outline_text:
        parts.append("## Document Outline\n")
        parts.append(outline_text)
        parts.append("")

    parts.append("## Document Info\n")
    parts.append(f"- Total pages: {total_pages}")
    parts.append("")

    if page_attributes_summary:
        parts.append("## Page Characteristics\n")
        parts.append(page_attributes_summary)
        parts.append("")

    if feedback_history:
        parts.append("## Prior Feedback Rounds\n")
        for i, prev in enumerate(feedback_history, 1):
            parts.append(f"### Round {i}\n")
            parts.append(prev)
            parts.append("")

    parts.append(
        "Decompose the reviewer feedback above into discrete revision tasks. "
        "Return a JSON object with `tasks` and `reasoning`."
    )

    return "\n".join(parts)


def build_page_attributes_summary(
    page_attributes: dict[int, PageAttributes],
) -> str:
    """Format page attributes into a compact summary string.

    Args:
        page_attributes: Mapping of page number to ``PageAttributes``.

    Returns:
        Multi-line summary, one line per page.
    """
    if not page_attributes:
        return ""

    lines: list[str] = []
    for page_num in sorted(page_attributes):
        attrs = page_attributes[page_num]
        flags: list[str] = [attrs.layout.value]
        if attrs.is_academic:
            flags.append("academic")
        if attrs.has_images:
            flags.append("images")
        if attrs.has_tables:
            flags.append("tables")
        if attrs.has_equations:
            flags.append("equations")
        if attrs.is_scanned:
            flags.append("scanned")
        lines.append(f"- Page {page_num}: {', '.join(flags)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Revision agent
# ---------------------------------------------------------------------------

REVISION_SYSTEM_PROMPT = """\
You are a document revision agent for a PDF-to-accessible-markdown pipeline. \
You receive specific revision tasks for a single page and must apply them \
using the tools provided.

## Tools

- **str_replace(old_text, new_text, reasoning, category)** — Replace exact \
text in the page markdown. `old_text` must appear exactly once. Include \
enough surrounding context to uniquely identify the location. Set category \
to "revision".
- **no_changes(confidence, notes)** — Call this if none of the assigned \
tasks require changes to this page's markdown. Provide your confidence \
level ("high", "medium", "low") and any observations.

## Guidelines

- Apply each task independently. Do not combine unrelated changes into one \
str_replace call.
- Preserve existing formatting (heading levels, list structure, code blocks) \
unless the task specifically asks you to change it.
- Be precise with old_text matching — copy the exact text including \
whitespace and punctuation.
- If a task cannot be applied (text not found, ambiguous location), call \
no_changes with notes explaining why.
- Do NOT invent content. Only make changes that are directly supported by \
the task descriptions.
"""


def build_revision_user_message(
    tasks: list[RevisionTask],
    page_num: int,
    current_markdown: str,
    outline_text: str,
    page_attrs: PageAttributes | None = None,
) -> str:
    """Build the text portion of the revision agent user message.

    The caller may prepend a ``BinaryContent`` image before this text.

    Args:
        tasks: Revision tasks assigned to this page.
        page_num: The page number being revised.
        current_markdown: Current markdown content of this page.
        outline_text: Pre-formatted document outline for context.
        page_attrs: Page attributes (if available) for additional context.

    Returns:
        Markdown-formatted user message.
    """
    parts: list[str] = []

    parts.append(f"## Page {page_num} — Revision Tasks\n")

    for task in tasks:
        parts.append(f"### Task {task.id}: {task.category.value}\n")
        parts.append(task.description)
        parts.append("")

    if outline_text:
        parts.append("## Document Outline\n")
        parts.append(outline_text)
        parts.append("")

    if page_attrs:
        flags: list[str] = [page_attrs.layout.value]
        if page_attrs.is_academic:
            flags.append("academic")
        if page_attrs.has_images:
            flags.append("images")
        if page_attrs.has_tables:
            flags.append("tables")
        if page_attrs.has_equations:
            flags.append("equations")
        if page_attrs.is_scanned:
            flags.append("scanned")
        parts.append("## Page Attributes\n")
        parts.append(f"- {', '.join(flags)}")
        parts.append("")

    parts.append("## Current Page Markdown\n")
    parts.append(f"```\n{current_markdown}\n```\n")

    parts.append(
        "Apply the revision tasks above using str_replace for each change, "
        "or call no_changes if no modifications are needed."
    )

    return "\n".join(parts)
