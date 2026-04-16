"""Public pipeline phase contract — server-side source of truth.

Mirrors ``PIPELINE_STAGES`` in ``clients/viewer/src/types/pipeline-viewer.ts``.
When adding, renaming, or reclassifying an internal step, update **both** this
module and the TypeScript constant in the same commit. ``tests/unit/shared/
test_pipeline_phases.py`` parses the TS file to enforce that they stay aligned.

Used by the SSE emitters to attach a ``user_phase`` field to every event so
consumers (the viewer, the WordPress plugin, API integrators) can drive their
progress UI off the public 5-phase contract without hardcoding the internal
step list.
"""

from __future__ import annotations

from typing import Final


PIPELINE_STAGES: Final[list[dict[str, object]]] = [
    {"name": "extraction",  "label": "Extraction",  "steps": ["docling", "docling_ocr"]},
    {"name": "analysis",    "label": "Analysis",    "steps": ["classification", "structure"]},
    {"name": "headings",    "label": "Headings",    "steps": ["heading_levels", "heading_reconciliation"]},
    {"name": "translation", "label": "Translation", "steps": ["page_content", "code_blocks"]},
    {"name": "assembly",    "label": "Assembly",    "steps": ["boundaries", "cleanup"]},
]

REVIEW_PHASE: Final[str] = "review"

_STEP_TO_PHASE: Final[dict[str, str]] = {
    step: stage["name"]  # type: ignore[misc]
    for stage in PIPELINE_STAGES
    for step in stage["steps"]  # type: ignore[attr-defined]
}


def user_phase_for_step(step_name: str) -> str:
    """Return the public phase name for an internal step.

    Steps not declared in ``PIPELINE_STAGES`` (e.g. ``revision_*``, ``feedback_*``,
    or any future custom step) fall back to ``"review"``, matching the viewer's
    dynamic Review stage.
    """
    return _STEP_TO_PHASE.get(step_name, REVIEW_PHASE)


VALID_USER_PHASES: Final[frozenset[str]] = frozenset(
    [stage["name"] for stage in PIPELINE_STAGES] + [REVIEW_PHASE]  # type: ignore[misc]
)
