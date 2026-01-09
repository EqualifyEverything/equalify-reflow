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
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


class DebugImageReference(BaseModel):
    """Reference to an image stored separately in the debug bundle.

    This is a standalone model to avoid circular imports with agents.models.
    It mirrors ImageReference but is used specifically for serialization.

    Attributes:
        ref_type: Type of image ("page", "element", "cropped")
        identifier: Unique identifier (page number or element target)
        path: Relative path in bundle (e.g., "images/page_001.png")
        media_type: MIME type of the image (default: "image/png")
        size_bytes: Original size of the image data in bytes
    """

    ref_type: str = Field(
        ...,
        description="Type of image: 'page' for full page, 'element' for cropped element",
    )
    identifier: str = Field(
        ...,
        description="Unique identifier (e.g., 'page_1', 'fig:1', 'table:2')",
    )
    path: str = Field(
        ...,
        description="Relative path in debug bundle (e.g., 'images/page_001.png')",
    )
    media_type: str = Field(
        default="image/png",
        description="MIME type of the image",
    )
    size_bytes: int = Field(
        default=0,
        description="Original size of the image data in bytes",
    )


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
        image_references: References to images stored separately in bundle
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
    image_references: list[DebugImageReference] = Field(
        default_factory=list,
        description="References to images stored separately in debug bundle",
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
    "DebugImageReference",
    "DebugPhaseSummary",
    "DebugBundleManifest",
]
