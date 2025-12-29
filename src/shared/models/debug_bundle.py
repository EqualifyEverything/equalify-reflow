"""Debug bundle models for pipeline observability.

These models support the debug bundle feature which allows developers
to download a complete artifact package for any job, containing:
- Original PDF and page images
- All agent prompts and responses
- Intermediate outputs at each phase
- Final results and observations

Usage:
    Submit a job with generate_debug_bundle=true, then download via:
    GET /api/documents/{job_id}/debug-bundle
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class DebugArtifact(BaseModel):
    """Single artifact from an agent execution.

    Captures the complete input/output of one agent call for debugging.

    Attributes:
        agent_name: Name of the agent (e.g., "layout", "extraction", "figures")
        phase: Pipeline phase ("analyze", "extract", "refine", "assemble")
        timestamp: When this agent was executed
        input_summary: JSON description of input (manifest, page refs, etc.)
        prompt: Full rendered prompt sent to LLM
        response_raw: Raw LLM response text
        output_parsed: Parsed/validated output as JSON string
        metadata: Execution metadata (tokens, cost, duration, model, etc.)
    """

    agent_name: str = Field(..., description="Agent identifier")
    phase: str = Field(..., description="Pipeline phase")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    input_summary: str = Field(default="", description="JSON description of input")
    prompt: str = Field(default="", description="Full prompt sent to LLM")
    response_raw: str = Field(default="", description="Raw LLM response")
    output_parsed: str = Field(default="", description="Parsed output as JSON")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Execution metadata (tokens, cost, model, duration)"
    )


class DebugPhaseSummary(BaseModel):
    """Summary of a single pipeline phase.

    Attributes:
        phase: Phase name
        started_at: When phase started
        completed_at: When phase completed
        duration_seconds: Total phase duration
        agents_run: List of agents executed in this phase
        total_tokens: Combined token usage
        total_cost_cents: Combined cost
        success: Whether phase completed successfully
        error: Error message if phase failed
    """

    phase: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float = 0.0
    agents_run: list[str] = Field(default_factory=list)
    total_tokens: int = 0
    total_cost_cents: float = 0.0
    success: bool = True
    error: str | None = None


class DebugBundleManifest(BaseModel):
    """Manifest for a complete debug bundle.

    This is the top-level metadata file included in the debug bundle zip.

    Attributes:
        job_id: Job identifier
        created_at: When bundle was generated
        document_name: Original filename
        total_pages: Number of pages in document
        status: Final job status
        total_duration_seconds: End-to-end processing time
        total_tokens: Combined token usage across all agents
        total_cost_cents: Combined cost across all agents
        phases: Summary of each pipeline phase
        agents_executed: Total count of agent executions
        artifacts_count: Number of artifact files in bundle
    """

    job_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    document_name: str = ""
    total_pages: int = 0
    status: str = "unknown"
    total_duration_seconds: float = 0.0
    total_tokens: int = 0
    total_cost_cents: float = 0.0
    phases: list[DebugPhaseSummary] = Field(default_factory=list)
    agents_executed: int = 0
    artifacts_count: int = 0


__all__ = [
    "DebugArtifact",
    "DebugPhaseSummary",
    "DebugBundleManifest",
]
