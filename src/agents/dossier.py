"""Pipeline Dossier — Accumulating Context for Multi-Phase Pipeline.

The dossier captures intelligence from each phase so downstream agents
have full context about the document and what has been done so far.

Pattern inspired by Shannon's deliverables/ directory approach, adapted
to work with PydanticAI's typed deps system instead of files.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .models import DocumentType, OutlineEntry


class PlanningSnapshot(BaseModel):
    """Captures intelligence from the planning phase."""

    document_title: str = Field(..., description="Document title from structure inference")
    document_type: DocumentType = Field(
        default=DocumentType.OTHER, description="Classified document type"
    )
    total_pages: int = Field(..., description="Total page count")
    outline: list[OutlineEntry] = Field(
        default_factory=list, description="Hierarchical outline"
    )
    dictionary: list[str] = Field(
        default_factory=list, description="Domain-specific terms for spell-checking"
    )
    page_summaries: dict[int, str] = Field(
        default_factory=dict, description="Page number -> 1-2 sentence summary"
    )
    section_map: dict[int, str] = Field(
        default_factory=dict,
        description="Page number -> deepest section heading (e.g., 'Section 2.1: Architecture')",
    )
    heading_fixes_count: int = Field(
        default=0, description="Number of heading level fixes planned"
    )
    figures_planned: dict[int, int] = Field(
        default_factory=dict, description="Page number -> figure count"
    )
    tables_planned: dict[int, int] = Field(
        default_factory=dict, description="Page number -> table count"
    )
class SubagentFinding(BaseModel):
    """A single finding from a subagent execution."""

    page: int = Field(..., description="Page number")
    task_type: str = Field(
        ..., description="Task type identifier (e.g., 'page_artifact', 'typography')"
    )
    description: str = Field(..., description="Human-readable finding summary")
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    applied: bool = Field(
        default=False, description="Whether the edit was applied by the parent agent"
    )
class ExecutionSnapshot(BaseModel):
    """Captures intelligence from the execution phase."""

    pages_processed: list[int] = Field(
        default_factory=list, description="Pages that were processed"
    )
    subagent_findings: dict[int, list[SubagentFinding]] = Field(
        default_factory=dict, description="Page number -> list of subagent findings"
    )
    edits_applied_count: int = Field(default=0, description="Total edits applied")
    tasks_completed: int = Field(default=0, description="Total tasks completed")
    tasks_failed: int = Field(default=0, description="Total tasks failed")
class VerificationSnapshot(BaseModel):
    """Captures intelligence from the verification phase."""

    passed: bool = Field(..., description="Whether document passed verification")
    pages_passed: list[int] = Field(
        default_factory=list, description="Pages that passed"
    )
    pages_failed: list[int] = Field(
        default_factory=list, description="Pages that failed"
    )
    critical_issues: list[str] = Field(
        default_factory=list, description="Critical issues found"
    )
    warnings: list[str] = Field(default_factory=list, description="Warnings found")
class PipelineDossier(BaseModel):
    """Accumulating intelligence dossier for the processing pipeline.

    Created at pipeline start, populated at each phase boundary, and
    threaded through deps to all downstream agents.
    """

    document_id: str = Field(..., description="Document identifier")
    filename: str = Field(..., description="Original filename")
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Dossier creation time"
    )
    current_phase: str = Field(default="init", description="Current pipeline phase")

    # Phase snapshots — populated as pipeline progresses
    planning: PlanningSnapshot | None = Field(
        default=None, description="Populated after planning phase"
    )
    execution: ExecutionSnapshot | None = Field(
        default=None, description="Populated after execution phase"
    )
    verification: VerificationSnapshot | None = Field(
        default=None, description="Populated after verification phase"
    )
