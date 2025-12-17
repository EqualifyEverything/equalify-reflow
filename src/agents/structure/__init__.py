"""Chained structure analysis agents.

This package contains the chained agents for structure analysis:
1. hierarchy_validator - Pure Python heading hierarchy validation
2. alignment_agent - Visual-semantic alignment (Haiku, conditional)
3. reading_order_agent - Reading order validation (Haiku, conditional)
4. coherence_agent - Cross-page coherence (Haiku, conditional)
5. structure_fix_agent - Semantic structural fixes (Haiku, Phase 3a)

The chained_structure.py orchestrator coordinates these steps.
"""

from src.agents.structure.alignment_agent import (
    AlignmentIssue,
    AlignmentOutput,
    check_alignment,
)
from src.agents.structure.hierarchy_validator import (
    HierarchyIssue,
    HierarchyValidation,
    validate_hierarchy,
)
from src.agents.structure.reading_order_agent import (
    ReadingOrderIssue,
    ReadingOrderOutput,
    check_reading_order,
)
from src.agents.structure.structure_fix_agent import (
    OCRDecision,
    StructureFixOutput,
    StructureFixResult,
    fix_structural_issues,
)

__all__ = [
    # Hierarchy (Pure Python)
    "HierarchyIssue",
    "HierarchyValidation",
    "validate_hierarchy",
    # Alignment
    "AlignmentIssue",
    "AlignmentOutput",
    "check_alignment",
    # Reading Order
    "ReadingOrderIssue",
    "ReadingOrderOutput",
    "check_reading_order",
    # Structure Fix (Phase 3a)
    "OCRDecision",
    "StructureFixOutput",
    "StructureFixResult",
    "fix_structural_issues",
]
