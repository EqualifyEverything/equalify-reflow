"""Processing result models for the 4-phase architecture.

ProcessingResult is the top-level API output containing:
- Final markdown with auto-corrections applied
- Full glass box processing trace
- Human review checklist

ProcessingTrace captures everything that happened during processing,
including phase summaries and per-agent traces.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .agent_trace import AgentTrace
from .review_checklist import ReviewChecklist


class AnalysisSummary(BaseModel):
    """Summary of Phase 1: Analysis.

    Captures what the analysis phase discovered about the document,
    including document type, key entities, and routing decisions.

    Attributes:
        document_type: Classified document type
        total_pages: Number of pages in document
        key_entities: Important names/terms extracted
        required_agents: Agents that will run based on content
        confidence: Confidence in analysis
        time_seconds: Execution time
        cost_cents: LLM cost

    Example:
        >>> summary = AnalysisSummary(
        ...     document_type="research_paper",
        ...     total_pages=9,
        ...     key_entities=["yt", "Enzo", "DVCS"],
        ...     required_agents=["figures", "tables", "typography"],
        ...     confidence=0.95,
        ...     time_seconds=12.5,
        ...     cost_cents=5.8
        ... )
    """

    document_type: str = Field(
        ...,
        description="Classified document type"
    )
    total_pages: int = Field(
        ...,
        ge=1,
        description="Number of pages in document"
    )
    key_entities: list[str] = Field(
        default_factory=list,
        description="Important names/terms extracted"
    )
    required_agents: list[str] = Field(
        default_factory=list,
        description="Agents that will run based on content"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in analysis"
    )
    time_seconds: float = Field(
        ...,
        ge=0.0,
        description="Execution time"
    )
    cost_cents: float = Field(
        ...,
        ge=0.0,
        description="LLM cost"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_type": "research_paper",
                "total_pages": 9,
                "key_entities": ["yt", "Enzo", "DVCS"],
                "required_agents": ["figures", "tables", "typography"],
                "confidence": 0.95,
                "time_seconds": 12.5,
                "cost_cents": 5.8
            }
        }
    )


class ExtractionSummary(BaseModel):
    """Summary of Phase 2: Extraction.

    Captures extraction phase metrics including confidence
    and any correction iterations performed.

    Attributes:
        confidence: Confidence in extraction quality
        pages_extracted: Number of pages processed
        correction_iterations: Number of extraction refinement iterations
        time_seconds: Execution time
        cost_cents: LLM cost

    Example:
        >>> summary = ExtractionSummary(
        ...     confidence=0.92,
        ...     pages_extracted=9,
        ...     correction_iterations=1,
        ...     time_seconds=45.2,
        ...     cost_cents=8.4
        ... )
    """

    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence in extraction quality"
    )
    pages_extracted: int = Field(
        ...,
        ge=0,
        description="Number of pages processed"
    )
    correction_iterations: int = Field(
        default=1,
        ge=0,
        description="Number of extraction refinement iterations"
    )
    time_seconds: float = Field(
        ...,
        ge=0.0,
        description="Execution time"
    )
    cost_cents: float = Field(
        ...,
        ge=0.0,
        description="LLM cost"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "confidence": 0.92,
                "pages_extracted": 9,
                "correction_iterations": 1,
                "time_seconds": 45.2,
                "cost_cents": 8.4
            }
        }
    )


class StructureSummary(BaseModel):
    """Summary of Phase 3a: Structure Verification Loop.

    Captures metrics from the structure verification loop including
    lint issues found/fixed and OCR corrections.

    Attributes:
        iterations: Number of verification loop iterations
        lint_issues_found: Total lint issues detected
        lint_issues_fixed: Lint issues auto-fixed
        ocr_suggestions_processed: OCR suggestions reviewed
        corrections_applied: Total corrections applied
        final_lint_clean: Whether final output passes lint
        time_seconds: Execution time
        cost_cents: LLM cost

    Example:
        >>> summary = StructureSummary(
        ...     iterations=2,
        ...     lint_issues_found=5,
        ...     lint_issues_fixed=5,
        ...     ocr_suggestions_processed=3,
        ...     corrections_applied=2,
        ...     final_lint_clean=True,
        ...     time_seconds=18.3,
        ...     cost_cents=4.2
        ... )
    """

    iterations: int = Field(
        ...,
        ge=1,
        description="Number of verification loop iterations"
    )
    lint_issues_found: int = Field(
        default=0,
        ge=0,
        description="Total lint issues detected"
    )
    lint_issues_fixed: int = Field(
        default=0,
        ge=0,
        description="Lint issues auto-fixed"
    )
    ocr_suggestions_processed: int = Field(
        default=0,
        ge=0,
        description="OCR suggestions reviewed"
    )
    corrections_applied: int = Field(
        default=0,
        ge=0,
        description="Total corrections applied"
    )
    final_lint_clean: bool = Field(
        ...,
        description="Whether final output passes lint"
    )
    time_seconds: float = Field(
        ...,
        ge=0.0,
        description="Execution time"
    )
    cost_cents: float = Field(
        ...,
        ge=0.0,
        description="LLM cost"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "iterations": 2,
                "lint_issues_found": 5,
                "lint_issues_fixed": 5,
                "ocr_suggestions_processed": 3,
                "corrections_applied": 2,
                "final_lint_clean": True,
                "time_seconds": 18.3,
                "cost_cents": 4.2
            }
        }
    )


class ProcessingTrace(BaseModel):
    """Glass box: Everything that happened during processing.

    ProcessingTrace captures the complete execution history of the
    4-phase pipeline, including phase summaries and per-agent traces.

    This provides full transparency for auditing and debugging.

    Attributes:
        analysis: Phase 1 summary
        extraction: Phase 2 summary
        structure: Phase 3a summary (structure loop)
        agents: Per-agent traces (Phase 3b specialized agents)
        total_observations: Total issues detected
        auto_corrections_applied: Corrections applied automatically
        review_items_generated: Items requiring human review
        total_cost_cents: Total LLM cost
        total_time_seconds: Total execution time
        total_tokens: Total tokens used

    Example:
        >>> trace = ProcessingTrace(
        ...     analysis=analysis_summary,
        ...     extraction=extraction_summary,
        ...     structure=structure_summary,
        ...     agents=[figures_trace, tables_trace],
        ...     total_observations=8,
        ...     auto_corrections_applied=5,
        ...     review_items_generated=3,
        ...     total_cost_cents=42.5,
        ...     total_time_seconds=120.0,
        ...     total_tokens=45000
        ... )
    """

    # Phase summaries
    analysis: AnalysisSummary = Field(
        ...,
        description="Phase 1: Analyze summary"
    )
    extraction: ExtractionSummary = Field(
        ...,
        description="Phase 2: Extract summary"
    )
    structure: StructureSummary = Field(
        ...,
        description="Phase 3a: Structure loop summary"
    )

    # Per-agent results (Phase 3b: Refine - specialized agents)
    agents: list[AgentTrace] = Field(
        default_factory=list,
        description="Per-agent traces"
    )

    # Aggregate stats
    total_observations: int = Field(
        default=0,
        ge=0,
        description="Total issues detected"
    )
    auto_corrections_applied: int = Field(
        default=0,
        ge=0,
        description="Corrections applied automatically"
    )
    review_items_generated: int = Field(
        default=0,
        ge=0,
        description="Items requiring human review"
    )
    total_cost_cents: float = Field(
        default=0.0,
        ge=0.0,
        description="Total LLM cost"
    )
    total_time_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Total execution time"
    )
    total_tokens: int = Field(
        default=0,
        ge=0,
        description="Total tokens used"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "analysis": {
                    "document_type": "research_paper",
                    "total_pages": 9,
                    "key_entities": ["yt", "Enzo"],
                    "required_agents": ["figures", "tables"],
                    "confidence": 0.95,
                    "time_seconds": 12.5,
                    "cost_cents": 5.8
                },
                "extraction": {
                    "confidence": 0.92,
                    "pages_extracted": 9,
                    "correction_iterations": 1,
                    "time_seconds": 45.2,
                    "cost_cents": 8.4
                },
                "structure": {
                    "iterations": 2,
                    "lint_issues_found": 5,
                    "lint_issues_fixed": 5,
                    "ocr_suggestions_processed": 3,
                    "corrections_applied": 2,
                    "final_lint_clean": True,
                    "time_seconds": 18.3,
                    "cost_cents": 4.2
                },
                "agents": [],
                "total_observations": 8,
                "auto_corrections_applied": 5,
                "review_items_generated": 3,
                "total_cost_cents": 42.5,
                "total_time_seconds": 120.0,
                "total_tokens": 45000
            }
        }
    )


class ProcessingResult(BaseModel):
    """Complete result of document processing - exposed via API.

    This is the top-level output of the 4-phase pipeline, containing:
    - Final markdown with auto-corrections applied
    - Full glass box processing trace
    - Human review checklist for remaining items

    Attributes:
        job_id: Associated job ID
        status: Processing outcome (completed, needs_review, failed)
        markdown: Final markdown with auto-corrections applied
        confidence: Overall confidence score
        processing_trace: Full glass box trace
        review_checklist: Human review interface
        created_at: When result was created
        processing_time_seconds: Total processing time

    Example:
        >>> result = ProcessingResult(
        ...     job_id="job-123",
        ...     status="needs_review",
        ...     markdown="# Document Title\\n\\n...",
        ...     confidence=0.87,
        ...     processing_trace=trace,
        ...     review_checklist=checklist,
        ...     processing_time_seconds=120.0
        ... )
    """

    job_id: str = Field(
        ...,
        description="Associated job ID"
    )
    status: Literal["completed", "needs_review", "failed"] = Field(
        ...,
        description="Processing outcome"
    )

    # The outputs
    markdown: str = Field(
        ...,
        description="Final markdown with auto-corrections applied"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence score"
    )

    # Glass box: Full transparency
    processing_trace: ProcessingTrace = Field(
        ...,
        description="Full glass box trace"
    )

    # Human review interface
    review_checklist: ReviewChecklist = Field(
        ...,
        description="Human review interface"
    )

    # Metadata
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When result was created"
    )
    processing_time_seconds: float = Field(
        ...,
        ge=0.0,
        description="Total processing time"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "abc-123",
                "status": "needs_review",
                "markdown": "# Document Title\n\n...",
                "confidence": 0.87,
                "processing_trace": {},
                "review_checklist": {},
                "created_at": "2024-12-10T10:35:00Z",
                "processing_time_seconds": 120.0
            }
        }
    )


__all__ = [
    "AnalysisSummary",
    "ExtractionSummary",
    "StructureSummary",
    "ProcessingTrace",
    "ProcessingResult",
]
