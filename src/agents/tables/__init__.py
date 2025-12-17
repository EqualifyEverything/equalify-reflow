"""Chained tables analysis agents.

This package contains the chained agents for table analysis:
1. structure_agent - Table structure detection (Haiku, multimodal)
2. accuracy_agent - Data accuracy assessment (Haiku, text-only)
3. routing - Pure Python routing logic

The chained_tables.py orchestrator coordinates these steps.
"""

from src.agents.tables.structure_agent import (
    TableStructure,
    TableStructureOutput,
    detect_structure,
)
from src.agents.tables.accuracy_agent import (
    DataAccuracy,
    DataAccuracyOutput,
    assess_accuracy,
)
from src.agents.tables.routing import route_table

__all__ = [
    # Structure
    "TableStructure",
    "TableStructureOutput",
    "detect_structure",
    # Accuracy
    "DataAccuracy",
    "DataAccuracyOutput",
    "assess_accuracy",
    # Routing
    "route_table",
]
