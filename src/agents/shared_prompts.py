"""Shared Prompt Utilities — Reusable prompt fragments derived from the dossier.

These functions read from the PipelineDossier to generate context-appropriate
prompt text for injection via PydanticAI's @agent.instructions decorators.

Each function returns an empty string if the dossier or required data is missing,
ensuring graceful degradation when dossier is not yet populated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .models import OutlineEntry

if TYPE_CHECKING:
    from .dossier import PipelineDossier


# =============================================================================
# Shared Constants
# =============================================================================

CONFIDENCE_THRESHOLD_GUIDE = """Confidence thresholds:
- >= 0.8: Apply edit automatically (needs_review=False)
- 0.5-0.8: Apply but flag for human review (needs_review=True)
- < 0.5: Skip edit, note the issue in output"""


# =============================================================================
# Section Map Derivation
# =============================================================================


def derive_section_map(outline: list[OutlineEntry]) -> dict[int, str]:
    """Walk the outline tree and map each page to its deepest section heading.

    For a page that falls within multiple nested sections, the deepest
    (most specific) heading wins.

    Args:
        outline: Hierarchical outline from document structure.

    Returns:
        Dict mapping page number to section string (e.g., "Section 2.1: Architecture").
    """
    section_map: dict[int, str] = {}

    def _walk(entries: list[OutlineEntry], depth: int = 0) -> None:
        for entry in entries:
            label = f"Section {'.' * 0}{entry.heading}"
            for page in range(entry.page_start, entry.page_end + 1):
                # Deeper entries overwrite shallower ones — last write wins
                # because children are processed after parents
                section_map[page] = label
            # Recurse into children (deeper = more specific)
            if entry.children:
                _walk(entry.children, depth + 1)

    _walk(outline)
    return section_map


# =============================================================================
# Prompt Fragment Formatters
# =============================================================================


def format_document_identity(dossier: PipelineDossier) -> str:
    """Format document title, type, and page count.

    Returns empty string if planning snapshot is not available.
    """
    p = dossier.planning
    if p is None:
        return ""
    return (
        f"Document: \"{p.document_title}\" ({p.document_type.value}), "
        f"{p.total_pages} pages."
    )


def format_section_context(dossier: PipelineDossier, page_num: int) -> str:
    """Format section context for a specific page.

    Returns empty string if section map is not available.
    """
    p = dossier.planning
    if p is None:
        return ""
    section = p.section_map.get(page_num)
    if not section:
        return ""
    return f"This page is in {section}."


def format_page_summary(dossier: PipelineDossier, page_num: int) -> str:
    """Format the planning-phase summary for a specific page.

    Returns empty string if page summary is not available.
    """
    p = dossier.planning
    if p is None:
        return ""
    summary = p.page_summaries.get(page_num)
    if not summary:
        return ""
    return f"Page summary: {summary}"


def format_dictionary_fragment(
    dossier: PipelineDossier, max_terms: int = 30
) -> str:
    """Format domain-specific terms from the document dictionary.

    Returns empty string if dictionary is empty.
    """
    p = dossier.planning
    if p is None:
        return ""
    terms = p.dictionary[:max_terms]
    if not terms:
        return ""
    return f"Domain terms: {', '.join(terms)}"


def format_verification_context(dossier: PipelineDossier) -> str:
    """Format prior verification results for recovery context.

    Returns empty string if verification snapshot is not available.
    """
    v = dossier.verification
    if v is None:
        return ""

    parts = [f"Prior verification: {'PASSED' if v.passed else 'FAILED'}."]
    if v.pages_failed:
        parts.append(f"Failed pages: {v.pages_failed}.")
    if v.critical_issues:
        issues_text = "; ".join(v.critical_issues[:5])
        parts.append(f"Critical issues: {issues_text}")
    if v.warnings:
        warnings_text = "; ".join(v.warnings[:5])
        parts.append(f"Warnings: {warnings_text}")
    return " ".join(parts)


def format_execution_findings(dossier: PipelineDossier, page_num: int) -> str:
    """Format what subagents found/applied on a specific page.

    Returns empty string if execution snapshot is not available.
    """
    e = dossier.execution
    if e is None:
        return ""
    findings = e.subagent_findings.get(page_num, [])
    if not findings:
        return ""

    lines = [f"Previous findings for page {page_num}:"]
    for f in findings:
        status = "applied" if f.applied else "skipped"
        lines.append(f"- [{f.task_type}] {f.description} ({status}, conf={f.confidence:.2f})")
    return "\n".join(lines)
