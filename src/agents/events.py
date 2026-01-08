"""V5 Pipeline Streaming Events.

Event types for real-time streaming to the UI via SSE.
Each event type corresponds to a stage in the pipeline.

Usage:
    Events are emitted by the orchestrator and workers,
    collected by the EventBus, and streamed to connected clients.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .models import (
    DocumentType,
    LedgerEntry,
    ProcessingStatus,
    RecoveryAction,
    RecoveryAttemptStatus,
    TaskType,
)

# =============================================================================
# Base Event
# =============================================================================


class StreamEvent(BaseModel):
    """Base class for all streaming events."""

    event_id: str = Field(
        default_factory=lambda: str(uuid4())[:12],
        description="Unique event identifier",
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    document_id: str = Field(..., description="Document being processed")

    def to_sse(self) -> dict[str, str]:
        """Format for Server-Sent Events."""
        return {
            "event": self.event_type,
            "data": self.model_dump_json(),
        }

    @property
    def event_type(self) -> str:
        """Event type string for SSE."""
        raise NotImplementedError


# =============================================================================
# Planning Phase Events
# =============================================================================


class PlanningStartedEvent(StreamEvent):
    """Emitted when planning phase begins."""

    total_pages: int = Field(..., description="Total pages in document")
    filename: str = Field(..., description="Original filename")

    @property
    def event_type(self) -> str:
        return "planning:started"


class PageScannedEvent(StreamEvent):
    """Emitted for each page during quick scan (Stage 1)."""

    page_num: int = Field(..., description="Page number scanned")
    headings_found: list[str] = Field(default_factory=list)
    figures: int = Field(default=0)
    tables: int = Field(default=0)
    page_type: str = Field(default="content")

    @property
    def event_type(self) -> str:
        return "planning:page_scanned"


class StructureInferredEvent(StreamEvent):
    """Emitted when document structure is inferred (Stage 2)."""

    document_title: str = Field(..., description="Detected document title")
    document_type: DocumentType = Field(..., description="Detected document type")
    outline: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Serialized outline",
    )
    dictionary_size: int = Field(default=0, description="Number of key terms found")
    heading_fixes_needed: int = Field(
        default=0,
        description="Number of heading fixes identified",
    )

    # Detailed fields for verbose output
    dictionary_terms: list[str] | None = Field(
        default=None,
        description="Actual key terms extracted",
    )
    heading_fixes: list[dict[str, Any]] | None = Field(
        default=None,
        description="Full HeadingFix objects with page, current_level, correct_level",
    )

    @property
    def event_type(self) -> str:
        return "planning:structure"


class PageSummarizedEvent(StreamEvent):
    """Emitted for each page after detailed summary (Stage 3)."""

    page_num: int = Field(..., description="Page number")
    summary: str = Field(default="", description="Page summary")
    section_context: str = Field(default="", description="Section this page belongs to")
    figures_found: int = Field(default=0)
    tables_found: int = Field(default=0)
    issues_found: int = Field(default=0)
    headings_found: int = Field(default=0, description="Number of headings on page")
    heading_fixes_needed: int = Field(default=0, description="Number of heading level corrections")

    # Detailed fields for verbose output
    figures_detail: list[dict[str, Any]] | None = Field(
        default=None,
        description="Full FigureContext objects (appears_to_be, surrounding_text, is_decorative)",
    )
    tables_detail: list[dict[str, Any]] | None = Field(
        default=None,
        description="Full TableContext objects (appears_to_be, estimated_rows, cols, caption)",
    )
    ocr_errors: list[str] | None = Field(
        default=None,
        description="OCR errors detected on this page",
    )
    formatting_issues: list[str] | None = Field(
        default=None,
        description="Formatting issues detected on this page",
    )
    keywords: list[str] | None = Field(
        default=None,
        description="Keywords extracted from this page",
    )

    @property
    def event_type(self) -> str:
        return "planning:page_summarized"


class JobCreatedEvent(StreamEvent):
    """Emitted for each job created (Stage 4)."""

    job_id: str = Field(..., description="Job identifier")
    job_type: str = Field(..., description="structure or content")
    page: int = Field(..., description="Page number")
    task_count: int = Field(..., description="Number of tasks in job")
    task_types: list[str] = Field(default_factory=list, description="Types of tasks")

    # Detailed fields for verbose output
    tasks_detail: list[dict[str, Any]] | None = Field(
        default=None,
        description="Full Task objects with target, context, priority",
    )
    section_context: str | None = Field(
        default=None,
        description="Section context for this job",
    )

    @property
    def event_type(self) -> str:
        return "job:created"


class PlanningCompleteEvent(StreamEvent):
    """Emitted when planning phase completes."""

    total_jobs: int = Field(..., description="Total jobs created")
    structure_jobs: int = Field(default=0, description="Structure-related jobs")
    content_jobs: int = Field(default=0, description="Content-related jobs")
    dictionary_terms: list[str] = Field(
        default_factory=list,
        description="Final dictionary",
    )
    duration_ms: int = Field(default=0, description="Planning duration")

    @property
    def event_type(self) -> str:
        return "planning:complete"


# =============================================================================
# Execution Phase Events
# =============================================================================


class JobStartedEvent(StreamEvent):
    """Emitted when a worker starts a job."""

    job_id: str = Field(..., description="Job identifier")
    page: int = Field(..., description="Page number")
    tasks: list[str] = Field(default_factory=list, description="Task summaries")
    worker_id: str = Field(default="", description="Worker handling this job")

    @property
    def event_type(self) -> str:
        return "job:started"


class EditProposedEvent(StreamEvent):
    """Emitted when a worker proposes an edit (before validation)."""

    job_id: str = Field(..., description="Job identifier")
    target: str = Field(..., description="Edit target (e.g., 'fig:1')")
    task_type: TaskType = Field(..., description="Type of edit")
    preview: str = Field(..., description="First 200 chars of new content")

    @property
    def event_type(self) -> str:
        return "edit:proposed"


class EditValidatedEvent(StreamEvent):
    """Emitted after edit passes/fails validation gate."""

    job_id: str = Field(..., description="Job identifier")
    target: str = Field(..., description="Edit target")
    approved: bool = Field(..., description="Whether edit was approved")
    feedback: str | None = Field(
        default=None,
        description="Validation feedback if rejected",
    )

    @property
    def event_type(self) -> str:
        return "edit:validated"


class EditCommittedEvent(StreamEvent):
    """Emitted when an edit is committed to the ledger."""

    ledger_entry: LedgerEntry = Field(..., description="The committed entry")
    content_preview: str = Field(
        ...,
        description="Full 'after' content for UI display",
    )

    @property
    def event_type(self) -> str:
        return "edit:committed"


class JobCompletedEvent(StreamEvent):
    """Emitted when a worker completes a job."""

    job_id: str = Field(..., description="Job identifier")
    page: int = Field(..., description="Page number")
    edits_made: int = Field(default=0, description="Number of successful edits")
    edits_rejected: int = Field(default=0, description="Number of rejected edits")
    duration_ms: int = Field(default=0, description="Job duration")
    tokens_used: int = Field(default=0, description="Tokens consumed")

    @property
    def event_type(self) -> str:
        return "job:completed"


class JobFailedEvent(StreamEvent):
    """Emitted when a job fails."""

    job_id: str = Field(..., description="Job identifier")
    page: int = Field(..., description="Page number")
    error: str = Field(..., description="Error message")
    recoverable: bool = Field(
        default=False,
        description="Whether job can be retried",
    )

    @property
    def event_type(self) -> str:
        return "job:failed"


class AgentThinkingEvent(StreamEvent):
    """Emitted for each message in agent conversation (prompts, responses, tool calls).

    This provides visibility into what the agent is doing step by step.
    """

    job_id: str = Field(..., description="Job identifier")
    agent_type: str = Field(..., description="Type of agent: worker, recovery, planner")
    step: int = Field(..., description="Message index in conversation (0-based)")

    # Message classification
    message_type: str = Field(
        ...,
        description="Message type: system, user, assistant, tool_call, tool_result",
    )
    content_preview: str = Field(
        default="",
        description="First 500 chars of message content",
    )
    full_content: str | None = Field(
        default=None,
        description="Full message content (for expansion)",
    )

    # Tool call details (if message_type is tool_call or tool_result)
    tool_name: str | None = Field(
        default=None,
        description="Name of tool called",
    )
    tool_args: dict[str, Any] | None = Field(
        default=None,
        description="Arguments passed to tool",
    )
    tool_result_preview: str | None = Field(
        default=None,
        description="Preview of tool result",
    )

    @property
    def event_type(self) -> str:
        return "agent:thinking"


# =============================================================================
# Verification Phase Events
# =============================================================================


class VerificationStartedEvent(StreamEvent):
    """Emitted when verification phase begins."""

    pages_to_verify: int = Field(..., description="Number of pages to verify")

    @property
    def event_type(self) -> str:
        return "verification:started"


class PageVerifiedEvent(StreamEvent):
    """Emitted for each page verified."""

    page_num: int = Field(..., description="Page number")
    passed: bool = Field(..., description="Whether page passed")
    issues: list[str] = Field(default_factory=list, description="Issues found")
    confidence: float = Field(default=0.8)

    @property
    def event_type(self) -> str:
        return "verification:page"


class VerificationCompleteEvent(StreamEvent):
    """Emitted when verification phase completes."""

    passed: bool = Field(..., description="Overall pass/fail")
    pages_passed: int = Field(default=0)
    pages_failed: int = Field(default=0)
    total_issues: int = Field(default=0)
    critical_issues: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0)

    @property
    def event_type(self) -> str:
        return "verification:complete"


# =============================================================================
# Recovery Phase Events
# =============================================================================


class RecoveryPhaseStartedEvent(StreamEvent):
    """Emitted when recovery phase begins."""

    pages_to_recover: list[int] = Field(
        ...,
        description="Pages that will be attempted for recovery",
    )
    max_attempts_per_page: int = Field(
        default=2,
        description="Maximum recovery attempts per page",
    )

    @property
    def event_type(self) -> str:
        return "recovery:started"


class RecoveryStartedEvent(StreamEvent):
    """Emitted when recovery begins for a specific page."""

    page_num: int = Field(..., description="Page number being recovered")
    attempt_number: int = Field(..., description="Which attempt (1 or 2)")
    issues: list[str] = Field(
        default_factory=list,
        description="Issues to be addressed",
    )

    @property
    def event_type(self) -> str:
        return "recovery:page_started"


class RecoveryEditAppliedEvent(StreamEvent):
    """Emitted when a recovery edit is applied."""

    page_num: int = Field(..., description="Page number")
    action: RecoveryAction = Field(..., description="Type of recovery action")
    target: str = Field(..., description="Target that was edited")
    reasoning: str = Field(..., description="Why this edit was made")

    @property
    def event_type(self) -> str:
        return "recovery:edit_applied"


class RecoveryCompleteEvent(StreamEvent):
    """Emitted when recovery completes for a specific page."""

    page_num: int = Field(..., description="Page number")
    attempt_number: int = Field(..., description="Which attempt completed")
    status: RecoveryAttemptStatus = Field(
        ...,
        description="Status of the recovery attempt",
    )
    edits_applied: int = Field(default=0, description="Number of edits applied")
    caveats: list[str] = Field(
        default_factory=list,
        description="Caveats if accepted with caveats",
    )

    @property
    def event_type(self) -> str:
        return "recovery:page_complete"


class RecoveryPhaseCompleteEvent(StreamEvent):
    """Emitted when recovery phase completes."""

    pages_recovered: list[int] = Field(
        default_factory=list,
        description="Pages that were successfully recovered",
    )
    pages_with_caveats: list[int] = Field(
        default_factory=list,
        description="Pages accepted with caveats",
    )
    pages_unrecoverable: list[int] = Field(
        default_factory=list,
        description="Pages that could not be recovered",
    )
    final_status: ProcessingStatus = Field(
        ...,
        description="Final processing status after recovery",
    )
    duration_ms: int = Field(default=0, description="Recovery phase duration")

    @property
    def event_type(self) -> str:
        return "recovery:complete"


# =============================================================================
# Final Pass Events (Pageless Optimization)
# =============================================================================
# Processing Complete Event
# =============================================================================


class ProcessingCompleteEvent(StreamEvent):
    """Emitted when entire pipeline completes."""

    success: bool = Field(..., description="Overall success")
    total_edits: int = Field(default=0)
    total_jobs: int = Field(default=0)
    total_cost: float = Field(default=0.0)
    total_duration_ms: int = Field(default=0)
    result_url: str = Field(default="", description="URL to fetch final result")

    @property
    def event_type(self) -> str:
        return "processing:complete"


class DoclingStartedEvent(StreamEvent):
    """Emitted when Docling PDF conversion starts."""

    filename: str = Field(..., description="PDF filename")

    @property
    def event_type(self) -> str:
        return "docling:started"


class DoclingCompleteEvent(StreamEvent):
    """Emitted when Docling PDF conversion completes."""

    pages_extracted: int = Field(..., description="Number of pages extracted")
    images_extracted: int = Field(..., description="Number of page images")
    duration_ms: int = Field(default=0, description="Conversion duration")
    is_scanned: bool = Field(default=False, description="True if PDF is scanned/image-based")
    chars_per_page: float = Field(default=0.0, description="Average chars per page from Docling")

    @property
    def event_type(self) -> str:
        return "docling:complete"


# =============================================================================
# Vision Extraction Events (for scanned PDFs)
# =============================================================================


class VisionExtractionStartedEvent(StreamEvent):
    """Emitted when vision-based extraction starts for scanned PDF."""

    total_pages: int = Field(..., description="Number of pages to extract")
    reason: str = Field(
        default="scanned_pdf",
        description="Why vision extraction is being used",
    )

    @property
    def event_type(self) -> str:
        return "vision:started"


class VisionPageExtractedEvent(StreamEvent):
    """Emitted when a page is extracted via vision."""

    page_num: int = Field(..., description="Page number extracted")
    chars_extracted: int = Field(default=0, description="Characters extracted")
    tokens_used: int = Field(default=0, description="Tokens consumed")
    success: bool = Field(default=True, description="Whether extraction succeeded")

    @property
    def event_type(self) -> str:
        return "vision:page_extracted"


class VisionExtractionCompleteEvent(StreamEvent):
    """Emitted when vision extraction completes."""

    pages_extracted: int = Field(..., description="Total pages extracted")
    total_chars: int = Field(default=0, description="Total characters extracted")
    total_tokens_input: int = Field(default=0, description="Total input tokens")
    total_tokens_output: int = Field(default=0, description="Total output tokens")
    estimated_cost: float = Field(default=0.0, description="Estimated cost in USD")
    duration_ms: int = Field(default=0, description="Total extraction duration")

    @property
    def event_type(self) -> str:
        return "vision:complete"


class ProcessingErrorEvent(StreamEvent):
    """Emitted when pipeline encounters a fatal error."""

    error: str = Field(..., description="Error message")
    phase: str = Field(..., description="Phase where error occurred")
    recoverable: bool = Field(default=False)

    @property
    def event_type(self) -> str:
        return "processing:error"


# =============================================================================
# Event Bus
# =============================================================================


class EventBus:
    """Simple event bus for collecting and streaming events.

    Usage:
        bus = EventBus(document_id)
        bus.emit(PlanningStartedEvent(...))

        # In SSE endpoint:
        async for event in bus.stream():
            yield event.to_sse()
    """

    # Maximum payload size for SSE events (64KB)
    MAX_PAYLOAD_SIZE = 64 * 1024

    def __init__(self, document_id: str):
        self.document_id = document_id
        self._events: list[StreamEvent] = []
        self._subscribers: list[Any] = []  # asyncio.Queue instances

    def emit(self, event: StreamEvent) -> None:
        """Emit an event to all subscribers.

        Automatically truncates large events to stay under MAX_PAYLOAD_SIZE.
        """
        # Check payload size and truncate if necessary
        try:
            payload = event.model_dump_json()
            if len(payload) > self.MAX_PAYLOAD_SIZE:
                event = self._truncate_large_fields(event)
        except Exception:
            pass  # If serialization fails, emit as-is

        self._events.append(event)
        for queue in self._subscribers:
            try:
                queue.put_nowait(event)
            except Exception:
                pass  # Queue full, skip

    def _truncate_large_fields(self, event: StreamEvent) -> StreamEvent:
        """Truncate large optional fields to stay under size limit."""
        data = event.model_dump()

        # Truncate fields that can be large
        truncate_fields = [
            "full_content",
            "conversation_json",
            "content_preview",
            "tool_result_preview",
        ]

        for field in truncate_fields:
            if field in data and data[field] and len(str(data[field])) > 2000:
                data[field] = str(data[field])[:2000] + "...[truncated]"

        # Truncate list fields
        list_fields = [
            "figures_detail",
            "tables_detail",
            "tasks_detail",
            "dictionary_terms",
        ]

        for field in list_fields:
            if field in data and data[field] and len(data[field]) > 20:
                data[field] = data[field][:20]
                data[field].append({"_truncated": True, "total": len(data[field])})

        return event.__class__(**data)

    def subscribe(self) -> Any:
        """Subscribe to events. Returns an asyncio.Queue."""
        import asyncio

        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: Any) -> None:
        """Unsubscribe from events."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    @property
    def events(self) -> list[StreamEvent]:
        """All events emitted so far."""
        return self._events.copy()

    def get_events_since(self, event_id: str) -> list[StreamEvent]:
        """Get events after a specific event ID (for reconnection)."""
        found = False
        result = []
        for event in self._events:
            if found:
                result.append(event)
            elif event.event_id == event_id:
                found = True
        return result


# =============================================================================
# Event Bus Registry
# =============================================================================

# Global registry for event buses, keyed by document/job ID
_event_registry: dict[str, EventBus] = {}


def register_event_bus(job_id: str, event_bus: EventBus) -> None:
    """Register an event bus for a job.

    Args:
        job_id: The job/document ID
        event_bus: The EventBus instance to register
    """
    _event_registry[job_id] = event_bus


def get_event_bus(job_id: str) -> EventBus | None:
    """Get the event bus for a job.

    Args:
        job_id: The job/document ID

    Returns:
        The EventBus instance if registered, None otherwise
    """
    return _event_registry.get(job_id)


def unregister_event_bus(job_id: str) -> None:
    """Unregister an event bus for a job.

    Args:
        job_id: The job/document ID to unregister
    """
    _event_registry.pop(job_id, None)
