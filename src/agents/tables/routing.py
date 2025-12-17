"""Pure Python routing logic for tables analysis.

Step 3 of the tables chain: Determine routing and action.
This is pure Python - NO LLM calls, zero cost.

Routing rules:
- merged_cells/nested/irregular → route: manual
- missing_headers → action: add_headers, route: auto
- data_issues → action: verify_data, route: manual
- no issues → action: none
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.agents.tables.accuracy_agent import DataAccuracy
from src.agents.tables.structure_agent import TableStructure
from src.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Routing Types
# =============================================================================


TableAction = Literal["add_headers", "restructure", "verify_data", "add_caption", "none"]
Route = Literal["auto", "manual"]
Severity = Literal["critical", "major", "minor"]


@dataclass
class TableRoutingResult:
    """Result of routing decision for a single table."""

    table_index: int
    complexity: str
    action: TableAction
    route: Route
    severity: Severity
    confidence: float
    visual_description: str
    issues: list[str]
    manual_reason: str | None


# =============================================================================
# Routing Logic (Pure Python)
# =============================================================================


def route_table(
    structure: TableStructure,
    accuracy: DataAccuracy | None,
) -> TableRoutingResult:
    """Route a single table based on structure and accuracy results.

    This is pure Python logic - no LLM calls, zero cost.

    Args:
        structure: Structure detection result from Step 1
        accuracy: Accuracy assessment from Step 2 (None for simple tables)

    Returns:
        TableRoutingResult with action, route, and severity
    """
    complexity = structure.complexity
    confidence = structure.confidence

    # Use accuracy confidence if available
    if accuracy is not None:
        confidence = min(confidence, accuracy.confidence)

    # Collect all issues
    issues: list[str] = []
    if structure.merged_cell_locations:
        issues.extend(structure.merged_cell_locations)
    if accuracy is not None and accuracy.issues:
        issues.extend(accuracy.issues)

    # Complex structures always need manual review
    if complexity in ["merged_cells", "nested", "irregular"]:
        action: TableAction = "restructure"
        if accuracy is not None and accuracy.accuracy == "structural_loss":
            severity: Severity = "critical"
        else:
            severity = "major"

        return TableRoutingResult(
            table_index=structure.table_index,
            complexity=complexity,
            action=action,
            route="manual",
            severity=severity,
            confidence=confidence,
            visual_description=structure.visual_description,
            issues=issues,
            manual_reason=f"Complex table structure ({complexity}) requires human judgment",
        )

    # Check for data accuracy issues
    if accuracy is not None:
        if accuracy.accuracy == "missing_data":
            return TableRoutingResult(
                table_index=structure.table_index,
                complexity=complexity,
                action="verify_data",
                route="manual",
                severity="critical",
                confidence=confidence,
                visual_description=structure.visual_description,
                issues=issues,
                manual_reason="Missing data detected in markdown representation",
            )

        if accuracy.accuracy == "structural_loss":
            return TableRoutingResult(
                table_index=structure.table_index,
                complexity=complexity,
                action="restructure",
                route="manual",
                severity="critical",
                confidence=confidence,
                visual_description=structure.visual_description,
                issues=issues,
                manual_reason="Table structure was lost in conversion",
            )

        if accuracy.missing_headers:
            # Missing headers is auto-fixable
            min_confidence = settings.min_confidence_for_auto_approval
            route: Route = "auto" if confidence >= min_confidence else "manual"
            manual_reason = None if route == "auto" else f"Low confidence ({confidence:.2f})"

            return TableRoutingResult(
                table_index=structure.table_index,
                complexity=complexity,
                action="add_headers",
                route=route,
                severity="major",
                confidence=confidence,
                visual_description=structure.visual_description,
                issues=issues,
                manual_reason=manual_reason,
            )

    # Check for missing headers in structure (if accuracy wasn't run)
    if not structure.has_headers:
        min_confidence = settings.min_confidence_for_auto_approval
        route = "auto" if confidence >= min_confidence else "manual"
        manual_reason = None if route == "auto" else f"Low confidence ({confidence:.2f})"

        return TableRoutingResult(
            table_index=structure.table_index,
            complexity=complexity,
            action="add_headers",
            route=route,
            severity="major",
            confidence=confidence,
            visual_description=structure.visual_description,
            issues=issues,
            manual_reason=manual_reason,
        )

    # No issues - table is fine
    return TableRoutingResult(
        table_index=structure.table_index,
        complexity=complexity,
        action="none",
        route="auto",
        severity="minor",
        confidence=confidence,
        visual_description=structure.visual_description,
        issues=[],
        manual_reason=None,
    )


def route_page_tables(
    structures: list[TableStructure],
    accuracies: list[DataAccuracy],
) -> list[TableRoutingResult]:
    """Route all tables on a page.

    Args:
        structures: All structure detection results for the page
        accuracies: Accuracy assessments (may not include simple tables)

    Returns:
        List of routing results for all tables
    """
    # Build lookup for accuracies by table index
    accuracy_by_index = {a.table_index: a for a in accuracies}

    results = []
    for structure in structures:
        accuracy = accuracy_by_index.get(structure.table_index)
        result = route_table(structure, accuracy)
        results.append(result)

    return results


__all__ = [
    "Route",
    "Severity",
    "TableAction",
    "TableRoutingResult",
    "route_page_tables",
    "route_table",
]
