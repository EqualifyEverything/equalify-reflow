"""Tables analysis agents.

This package contains the TablesAgent for table analysis and enhancement.

The TablesAgent (new architecture):
- Single merged agent with validation loop
- Outputs AgentResult directly
- Replaces the old chained approach

Reference: PRD-024 - Tables Agent Merge & Validation Loop
"""

# Legacy exports for backwards compatibility during transition
# TODO: Remove these after updating all consumers
from src.agents.tables.accuracy_agent import (
    DataAccuracy,
    DataAccuracyOutput,
    assess_accuracy,
)
from src.agents.tables.routing import route_table
from src.agents.tables.structure_agent import (
    TableStructure,
    TableStructureOutput,
    detect_structure,
)
from src.agents.tables.tables_agent import (
    TableEnhanceOutput,
    TablePlaceholder,
    TablesAgent,
    TableValidationIssue,
)

__all__ = [
    # New architecture (PRD-024)
    "TablesAgent",
    "TablePlaceholder",
    "TableValidationIssue",
    "TableEnhanceOutput",
    # Legacy (for backwards compatibility)
    "TableStructure",
    "TableStructureOutput",
    "detect_structure",
    "DataAccuracy",
    "DataAccuracyOutput",
    "assess_accuracy",
    "route_table",
]
