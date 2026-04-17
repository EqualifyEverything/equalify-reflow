"""Tests for the public pipeline phase mapping.

Covers:
- Every internal step name used in the SSE emitters maps to a valid public phase.
- Unknown steps fall back to the ``review`` catch-all.
- The Python ``PIPELINE_STAGES`` constant stays structurally aligned with the
  viewer's TypeScript source of truth.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.shared.pipeline_phases import (
    PIPELINE_STAGES,
    REVIEW_PHASE,
    VALID_USER_PHASES,
    user_phase_for_step,
)

pytestmark = pytest.mark.unit


# Every internal step name the pipeline ever emits via SSE (from
# src/api/pipeline_viewer.py and src/services/pipeline_viewer.py). Kept
# explicit so a silent rename on the emit side breaks this test.
ALL_INTERNAL_STEPS = [
    "docling",
    "docling_ocr",
    "classification",
    "structure",
    "heading_reconciliation",
    "heading_levels",
    "page_content",
    "code_blocks",
    "boundaries",
    "cleanup",
]


class TestUserPhaseLookup:
    def test_every_internal_step_maps_to_a_valid_public_phase(self):
        """Acceptance-criterion test: every internal step emits a valid user_phase."""
        for step in ALL_INTERNAL_STEPS:
            phase = user_phase_for_step(step)
            assert phase in VALID_USER_PHASES, (
                f"step {step!r} mapped to invalid user_phase {phase!r}"
            )
            # Internal steps that are declared in PIPELINE_STAGES must not
            # fall through to the review catch-all.
            assert phase != REVIEW_PHASE, (
                f"declared step {step!r} fell through to {REVIEW_PHASE!r}"
            )

    @pytest.mark.parametrize(
        "step,expected",
        [
            ("docling", "extraction"),
            ("docling_ocr", "extraction"),
            ("classification", "analysis"),
            ("structure", "analysis"),
            ("heading_levels", "headings"),
            ("heading_reconciliation", "headings"),
            ("page_content", "translation"),
            ("code_blocks", "translation"),
            ("boundaries", "assembly"),
            ("cleanup", "assembly"),
        ],
    )
    def test_known_steps_resolve_to_expected_phase(self, step: str, expected: str):
        assert user_phase_for_step(step) == expected

    @pytest.mark.parametrize(
        "step",
        ["revision_1", "feedback_pass", "some_future_step", "", "pipeline"],
    )
    def test_unknown_steps_fall_back_to_review(self, step: str):
        assert user_phase_for_step(step) == REVIEW_PHASE

    def test_no_step_appears_in_two_phases(self):
        """Sanity: the mapping is a function, not a relation."""
        seen: dict[str, str] = {}
        for stage in PIPELINE_STAGES:
            phase_name = stage["name"]
            for step in stage["steps"]:
                assert step not in seen, (
                    f"step {step!r} appears in both {seen[step]!r} and {phase_name!r}"
                )
                seen[step] = phase_name


TS_FILE = (
    Path(__file__).resolve().parents[3]
    / "clients"
    / "viewer"
    / "src"
    / "types"
    / "pipeline-viewer.ts"
)


@pytest.mark.skipif(
    not TS_FILE.exists(),
    reason=(
        "clients/viewer is not mounted in the api-gateway container; "
        "alignment check runs locally or in a CI job that has the full repo."
    ),
)
class TestTypeScriptSourceOfTruthAlignment:
    """The Python mapping must structurally match the TS PIPELINE_STAGES.

    Without this, a frontend-only rename would silently desync the server-side
    enrichment and break consumers that trust the user_phase field.
    """

    def test_python_mapping_matches_typescript(self):
        """Parse PIPELINE_STAGES from the TS file and diff it against Python."""
        text = TS_FILE.read_text(encoding="utf-8")
        ts_pairs = _parse_ts_pipeline_stages(text)

        py_pairs = [
            (stage["name"], tuple(stage["steps"]))
            for stage in PIPELINE_STAGES
        ]

        assert py_pairs == ts_pairs, (
            "PIPELINE_STAGES in Python and TypeScript are out of sync.\n"
            f"Python: {py_pairs}\n"
            f"TS:     {ts_pairs}\n"
            "Update src/shared/pipeline_phases.py and "
            "clients/viewer/src/types/pipeline-viewer.ts together."
        )


def _parse_ts_pipeline_stages(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Minimal parser for the ``PIPELINE_STAGES`` const literal in the TS file.

    Tolerates whitespace variation but assumes the object-literal shape
    ``{ name: '...', label: '...', steps: ['...', '...'] }``.
    """
    match = re.search(
        r"export const PIPELINE_STAGES[^=]*=\s*\[(.*?)\];",
        text,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("could not locate PIPELINE_STAGES in TS source")
    body = match.group(1)

    entry_re = re.compile(
        r"\{\s*name:\s*'([^']+)'\s*,\s*label:\s*'[^']+'\s*,\s*"
        r"steps:\s*\[([^\]]*)\]\s*,?\s*\}",
        re.DOTALL,
    )
    results: list[tuple[str, tuple[str, ...]]] = []
    for m in entry_re.finditer(body):
        name = m.group(1)
        steps_str = m.group(2)
        steps = tuple(s.strip().strip("'") for s in steps_str.split(",") if s.strip())
        results.append((name, steps))
    if not results:
        raise AssertionError("failed to parse any PIPELINE_STAGES entries")
    return results
