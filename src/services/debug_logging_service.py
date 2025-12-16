"""Debug logging service for comprehensive system observability.

This service provides centralized debug logging capabilities including:
- API input/output logging
- LLM prompt and response logging
- Processing phase transitions
- System event logging
- Latency tracking and analysis

All debug logs are structured with correlation IDs (job_id) for traceability.
Enable debug mode by setting DEBUG_MODE=true in environment.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from ..config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Latency Tracking
# =============================================================================


@dataclass
class LLMLatencyMetrics:
    """Latency metrics for a single LLM call.

    Captures timing and throughput for understanding LLM performance.
    """

    agent_name: str
    duration_ms: float
    input_tokens: int
    output_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def output_tokens_per_second(self) -> float:
        """Model generation speed - key throughput metric."""
        if self.duration_ms <= 0:
            return 0.0
        return (self.output_tokens / self.duration_ms) * 1000

    @property
    def total_tokens_per_second(self) -> float:
        """Total throughput including input processing."""
        if self.duration_ms <= 0:
            return 0.0
        return (self.total_tokens / self.duration_ms) * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "duration_ms": round(self.duration_ms, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "output_tokens_per_second": round(self.output_tokens_per_second, 1),
            "total_tokens_per_second": round(self.total_tokens_per_second, 1),
        }


@dataclass
class PhaseLatencyMetrics:
    """Latency metrics for a processing phase."""

    phase_name: str
    phase_number: int
    duration_ms: float
    llm_calls: list[LLMLatencyMetrics] = field(default_factory=list)

    @property
    def llm_time_ms(self) -> float:
        """Total time spent in LLM calls."""
        return sum(call.duration_ms for call in self.llm_calls)

    @property
    def overhead_ms(self) -> float:
        """Time spent outside LLM calls (data prep, parsing, etc)."""
        return max(0, self.duration_ms - self.llm_time_ms)

    @property
    def llm_efficiency(self) -> float:
        """Percentage of phase time spent in LLM calls."""
        if self.duration_ms <= 0:
            return 0.0
        return (self.llm_time_ms / self.duration_ms) * 100

    @property
    def total_input_tokens(self) -> int:
        return sum(call.input_tokens for call in self.llm_calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(call.output_tokens for call in self.llm_calls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase_name": self.phase_name,
            "phase_number": self.phase_number,
            "duration_ms": round(self.duration_ms, 2),
            "llm_time_ms": round(self.llm_time_ms, 2),
            "overhead_ms": round(self.overhead_ms, 2),
            "llm_efficiency_pct": round(self.llm_efficiency, 1),
            "llm_call_count": len(self.llm_calls),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


@dataclass
class JobLatencyTracker:
    """Tracks latency metrics across an entire job's lifecycle.

    Use this to understand where time is spent in document processing.

    Example:
        tracker = JobLatencyTracker(job_id="abc123")
        tracker.start_phase("analysis", 1)
        tracker.record_llm_call("analysis_agent", 1500.0, 1000, 500)
        tracker.end_phase()
        summary = tracker.get_summary()
    """

    job_id: str
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    phases: list[PhaseLatencyMetrics] = field(default_factory=list)
    _current_phase: PhaseLatencyMetrics | None = field(default=None, repr=False)
    _phase_start_time: float | None = field(default=None, repr=False)

    def start_phase(self, phase_name: str, phase_number: int) -> None:
        """Mark the start of a processing phase."""
        self._phase_start_time = time.time()
        self._current_phase = PhaseLatencyMetrics(
            phase_name=phase_name,
            phase_number=phase_number,
            duration_ms=0,
        )

    def record_llm_call(
        self,
        agent_name: str,
        duration_ms: float,
        input_tokens: int,
        output_tokens: int,
    ) -> LLMLatencyMetrics:
        """Record an LLM call within the current phase."""
        metrics = LLMLatencyMetrics(
            agent_name=agent_name,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        if self._current_phase is not None:
            self._current_phase.llm_calls.append(metrics)
        return metrics

    def end_phase(self) -> PhaseLatencyMetrics | None:
        """Mark the end of the current phase and return its metrics."""
        if self._current_phase is None or self._phase_start_time is None:
            return None

        duration_ms = (time.time() - self._phase_start_time) * 1000
        self._current_phase.duration_ms = duration_ms
        self.phases.append(self._current_phase)

        result = self._current_phase
        self._current_phase = None
        self._phase_start_time = None
        return result

    def finish(self) -> None:
        """Mark the job as complete."""
        self.end_time = time.time()

    @property
    def total_duration_ms(self) -> float:
        """Total job duration in milliseconds."""
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    @property
    def total_llm_time_ms(self) -> float:
        """Total time spent in LLM calls across all phases."""
        return sum(phase.llm_time_ms for phase in self.phases)

    @property
    def total_overhead_ms(self) -> float:
        """Total time spent outside LLM calls."""
        return self.total_duration_ms - self.total_llm_time_ms

    @property
    def llm_efficiency(self) -> float:
        """Percentage of total time spent in LLM calls."""
        if self.total_duration_ms <= 0:
            return 0.0
        return (self.total_llm_time_ms / self.total_duration_ms) * 100

    @property
    def total_llm_calls(self) -> int:
        """Total number of LLM calls across all phases."""
        return sum(len(phase.llm_calls) for phase in self.phases)

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens across all LLM calls."""
        return sum(phase.total_input_tokens for phase in self.phases)

    @property
    def total_output_tokens(self) -> int:
        """Total output tokens across all LLM calls."""
        return sum(phase.total_output_tokens for phase in self.phases)

    @property
    def average_output_tokens_per_second(self) -> float:
        """Average model generation speed across all calls."""
        if self.total_llm_time_ms <= 0:
            return 0.0
        return (self.total_output_tokens / self.total_llm_time_ms) * 1000

    def get_summary(self) -> dict[str, Any]:
        """Get a comprehensive latency summary for the job."""
        return {
            "job_id": self.job_id,
            "timing": {
                "total_duration_ms": round(self.total_duration_ms, 2),
                "total_duration_seconds": round(self.total_duration_ms / 1000, 2),
                "llm_time_ms": round(self.total_llm_time_ms, 2),
                "overhead_ms": round(self.total_overhead_ms, 2),
                "llm_efficiency_pct": round(self.llm_efficiency, 1),
            },
            "throughput": {
                "total_llm_calls": self.total_llm_calls,
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "total_tokens": self.total_input_tokens + self.total_output_tokens,
                "avg_output_tokens_per_second": round(
                    self.average_output_tokens_per_second, 1
                ),
            },
            "phases": [phase.to_dict() for phase in self.phases],
            "slowest_phase": self._get_slowest_phase(),
            "slowest_llm_call": self._get_slowest_llm_call(),
        }

    def _get_slowest_phase(self) -> dict[str, Any] | None:
        """Identify the slowest phase."""
        if not self.phases:
            return None
        slowest = max(self.phases, key=lambda p: p.duration_ms)
        return {
            "phase_name": slowest.phase_name,
            "duration_ms": round(slowest.duration_ms, 2),
            "pct_of_total": round(
                (slowest.duration_ms / self.total_duration_ms) * 100, 1
            )
            if self.total_duration_ms > 0
            else 0,
        }

    def _get_slowest_llm_call(self) -> dict[str, Any] | None:
        """Identify the slowest individual LLM call."""
        all_calls = [call for phase in self.phases for call in phase.llm_calls]
        if not all_calls:
            return None
        slowest = max(all_calls, key=lambda c: c.duration_ms)
        return {
            "agent_name": slowest.agent_name,
            "duration_ms": round(slowest.duration_ms, 2),
            "output_tokens": slowest.output_tokens,
            "tokens_per_second": round(slowest.output_tokens_per_second, 1),
        }


# Global registry of active job trackers
_job_trackers: dict[str, JobLatencyTracker] = {}
_trackers_lock = threading.Lock()


def get_job_tracker(job_id: str) -> JobLatencyTracker | None:
    """Get an existing job latency tracker.

    Args:
        job_id: Job ID to look up

    Returns:
        JobLatencyTracker if found, None otherwise
    """
    with _trackers_lock:
        return _job_trackers.get(job_id)


def create_job_tracker(job_id: str) -> JobLatencyTracker:
    """Create a new job latency tracker.

    Args:
        job_id: Job ID to track

    Returns:
        New JobLatencyTracker instance
    """
    with _trackers_lock:
        tracker = JobLatencyTracker(job_id=job_id)
        _job_trackers[job_id] = tracker
        return tracker


def remove_job_tracker(job_id: str) -> JobLatencyTracker | None:
    """Remove and return a job tracker.

    Args:
        job_id: Job ID to remove

    Returns:
        Removed tracker if found, None otherwise
    """
    with _trackers_lock:
        return _job_trackers.pop(job_id, None)


class DebugEventType(str, Enum):
    """Types of debug events that can be logged."""

    # API Events
    API_REQUEST = "api_request"
    API_RESPONSE = "api_response"

    # LLM Events
    PROMPT_SENT = "prompt_sent"
    RESPONSE_RECEIVED = "response_received"

    # Processing Events
    PHASE_START = "phase_start"
    PHASE_END = "phase_end"
    PHASE_ERROR = "phase_error"

    # System Events
    SYSTEM_EVENT = "system_event"
    JOB_QUEUED = "job_queued"
    JOB_STARTED = "job_started"
    JOB_COMPLETED = "job_completed"
    JOB_FAILED = "job_failed"

    # Worker Events
    WORKER_STARTED = "worker_started"
    WORKER_STOPPED = "worker_stopped"
    WORKER_PROCESSING = "worker_processing"


class DebugLoggingService:
    """Centralized service for debug logging.

    This service provides methods to log various types of debug events
    with consistent formatting and correlation IDs.

    Example usage:
        debug_service = DebugLoggingService()
        debug_service.log_prompt(
            job_id="abc123",
            agent_name="analysis_agent",
            system_prompt="You are an assistant...",
            user_message="Analyze this document...",
            image_info={"size_bytes": 12345, "format": "png"}
        )
    """

    _instance: "DebugLoggingService | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "DebugLoggingService":
        """Singleton pattern for consistent debug logging across the app."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        """Initialize the debug logging service."""
        if self._initialized:
            return

        self._initialized = True
        self._file_handler: logging.FileHandler | None = None
        self._debug_logger = logging.getLogger("debug.equalify")

        # Configure debug logger
        self._debug_logger.setLevel(logging.DEBUG)
        self._debug_logger.propagate = False  # Don't propagate to root logger

        # Console handler for debug logs
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        if settings.debug_log_format == "json":
            console_handler.setFormatter(logging.Formatter("%(message)s"))
        else:
            console_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [DEBUG] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
                )
            )
        self._debug_logger.addHandler(console_handler)

        # File handler if configured
        if settings.debug_log_file:
            try:
                log_path = Path(settings.debug_log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                self._file_handler = logging.FileHandler(log_path, mode="a")
                self._file_handler.setLevel(logging.DEBUG)
                self._file_handler.setFormatter(logging.Formatter("%(message)s"))
                self._debug_logger.addHandler(self._file_handler)
                logger.info(f"Debug logging to file: {log_path}")
            except Exception as e:
                logger.error(f"Failed to initialize debug log file: {e}")

        if settings.debug_mode:
            logger.info(
                f"Debug mode enabled (format={settings.debug_log_format}, "
                f"prompts={settings.debug_log_prompts}, "
                f"responses={settings.debug_log_responses}, "
                f"images={settings.debug_log_images})"
            )

    @property
    def enabled(self) -> bool:
        """Check if debug mode is enabled."""
        return settings.debug_mode

    @property
    def images_enabled(self) -> bool:
        """Check if image saving is enabled."""
        return settings.debug_mode and settings.debug_save_images

    def save_debug_image(
        self,
        job_id: str,
        agent_name: str,
        image_bytes: bytes,
        page_num: int | None = None,
        image_index: int | None = None,
        suffix: str = "",
    ) -> str | None:
        """Save an image to the debug output directory.

        Images are saved with consistent naming:
        {debug_images_dir}/{job_id}/{agent_name}/page_{page_num}_{image_index}{suffix}.png

        Args:
            job_id: Job ID for directory organization
            agent_name: Agent name for subdirectory
            image_bytes: Raw PNG image bytes
            page_num: Page number (1-indexed)
            image_index: Image index on the page (for multiple images per page)
            suffix: Optional suffix for the filename

        Returns:
            Path to saved image, or None if saving is disabled/failed
        """
        if not self.images_enabled:
            return None

        try:
            # Build directory path: {base}/{job_id}/{agent_name}/
            images_dir = Path(settings.debug_images_dir)
            job_dir = images_dir / job_id / agent_name
            job_dir.mkdir(parents=True, exist_ok=True)

            # Build filename with consistent naming
            parts = ["page"]
            if page_num is not None:
                parts.append(str(page_num))
            if image_index is not None:
                parts.append(f"img{image_index}")
            if suffix:
                parts.append(suffix)

            filename = "_".join(parts) + ".png"
            file_path = job_dir / filename

            # Write image bytes
            file_path.write_bytes(image_bytes)

            logger.debug(f"Saved debug image: {file_path} ({len(image_bytes)} bytes)")

            return str(file_path)

        except Exception as e:
            logger.error(f"Failed to save debug image: {e}")
            return None

    def save_debug_images_batch(
        self,
        job_id: str,
        agent_name: str,
        images: list[tuple[bytes, int]],
    ) -> list[str]:
        """Save multiple page images to the debug output directory.

        Args:
            job_id: Job ID for directory organization
            agent_name: Agent name for subdirectory
            images: List of (image_bytes, page_num) tuples

        Returns:
            List of paths to saved images
        """
        if not self.images_enabled:
            return []

        saved_paths = []
        for image_bytes, page_num in images:
            path = self.save_debug_image(
                job_id=job_id,
                agent_name=agent_name,
                image_bytes=image_bytes,
                page_num=page_num,
            )
            if path:
                saved_paths.append(path)

        if saved_paths:
            logger.info(
                f"Saved {len(saved_paths)} debug images for job {job_id}/{agent_name}"
            )

        return saved_paths

    def _truncate(self, text: str | None, max_length: int | None = None) -> str | None:
        """Truncate text to configured maximum length.

        Args:
            text: Text to truncate
            max_length: Maximum length (uses settings.debug_truncate_length if None)

        Returns:
            Truncated text with indicator if truncated, or None if input is None
        """
        if text is None:
            return None

        max_len = max_length or settings.debug_truncate_length
        if len(text) <= max_len:
            return text

        return text[:max_len] + f"... [TRUNCATED - {len(text) - max_len} chars omitted]"

    def _format_event(
        self,
        event_type: DebugEventType,
        job_id: str | None,
        data: dict[str, Any],
    ) -> str:
        """Format a debug event for logging.

        Args:
            event_type: Type of debug event
            job_id: Job ID for correlation (can be None for system events)
            data: Event-specific data

        Returns:
            Formatted event string (JSON or text based on settings)
        """
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type.value,
            "job_id": job_id,
            **data,
        }

        if settings.debug_log_format == "json":
            return json.dumps(event, default=str, ensure_ascii=False)
        else:
            # Text format for human readability
            lines = [
                f"{'=' * 80}",
                f"Event: {event_type.value}",
                f"Time: {event['timestamp']}",
                f"Job ID: {job_id or 'N/A'}",
            ]
            for key, value in data.items():
                if isinstance(value, dict):
                    lines.append(f"{key}:")
                    for k, v in value.items():
                        lines.append(f"  {k}: {v}")
                elif isinstance(value, str) and len(value) > 200:
                    lines.append(f"{key}: (length={len(value)})")
                    lines.append(f"  {value[:500]}...")
                else:
                    lines.append(f"{key}: {value}")
            lines.append("=" * 80)
            return "\n".join(lines)

    def _log(
        self,
        event_type: DebugEventType,
        job_id: str | None,
        data: dict[str, Any],
    ) -> None:
        """Log a debug event if debug mode is enabled.

        Args:
            event_type: Type of debug event
            job_id: Job ID for correlation
            data: Event-specific data
        """
        if not self.enabled:
            return

        try:
            formatted = self._format_event(event_type, job_id, data)
            self._debug_logger.debug(formatted)
        except Exception as e:
            logger.error(f"Failed to log debug event: {e}")

    def log_api_request(
        self,
        job_id: str | None,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | str | None = None,
        query_params: dict[str, str] | None = None,
        client_ip: str | None = None,
    ) -> None:
        """Log an incoming API request.

        Args:
            job_id: Job ID if available
            method: HTTP method
            path: Request path
            headers: Request headers (sensitive headers will be masked)
            body: Request body
            query_params: Query parameters
            client_ip: Client IP address
        """
        # Mask sensitive headers
        safe_headers = None
        if headers:
            safe_headers = {
                k: "***" if k.lower() in ("authorization", "x-api-key") else v
                for k, v in headers.items()
            }

        self._log(
            DebugEventType.API_REQUEST,
            job_id,
            {
                "method": method,
                "path": path,
                "headers": safe_headers,
                "body": self._truncate(str(body)) if body else None,
                "query_params": query_params,
                "client_ip": client_ip,
            },
        )

    def log_api_response(
        self,
        job_id: str | None,
        method: str,
        path: str,
        status_code: int,
        response_body: dict[str, Any] | str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Log an API response.

        Args:
            job_id: Job ID if available
            method: HTTP method
            path: Request path
            status_code: HTTP status code
            response_body: Response body
            duration_ms: Request duration in milliseconds
        """
        self._log(
            DebugEventType.API_RESPONSE,
            job_id,
            {
                "method": method,
                "path": path,
                "status_code": status_code,
                "response_body": self._truncate(str(response_body)) if response_body else None,
                "duration_ms": duration_ms,
            },
        )

    def log_prompt(
        self,
        job_id: str,
        agent_name: str,
        system_prompt: str | None = None,
        user_message: str | None = None,
        image_info: dict[str, Any] | None = None,
        model_id: str | None = None,
        model_tier: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        """Log an LLM prompt being sent.

        Args:
            job_id: Job ID for correlation
            agent_name: Name of the agent sending the prompt
            system_prompt: System prompt content
            user_message: User message content
            image_info: Information about attached image (not the binary data)
            model_id: Model identifier
            model_tier: Model tier (reasoning/efficient)
            temperature: Temperature setting
            max_tokens: Max tokens setting
        """
        data: dict[str, Any] = {
            "agent_name": agent_name,
            "model_id": model_id,
            "model_tier": model_tier,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if settings.debug_log_prompts:
            data["system_prompt"] = self._truncate(system_prompt)
            data["user_message"] = self._truncate(user_message)
        else:
            data["system_prompt_length"] = len(system_prompt) if system_prompt else 0
            data["user_message_length"] = len(user_message) if user_message else 0

        if settings.debug_log_images and image_info:
            data["image_info"] = image_info

        self._log(DebugEventType.PROMPT_SENT, job_id, data)

    def log_response(
        self,
        job_id: str,
        agent_name: str,
        response_text: str | None = None,
        parsed_output: Any = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_cents: float | None = None,
        duration_ms: float | None = None,
        model_id: str | None = None,
    ) -> None:
        """Log an LLM response received.

        Also automatically records the LLM call for latency tracking if
        duration_ms and token counts are provided.

        Args:
            job_id: Job ID for correlation
            agent_name: Name of the agent that received the response
            response_text: Raw response text from LLM
            parsed_output: Parsed/structured output
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            total_tokens: Total tokens used
            estimated_cost_cents: Estimated cost in cents
            duration_ms: Response time in milliseconds
            model_id: Model identifier
        """
        # Auto-record LLM call for latency tracking when we have the data
        if (
            duration_ms is not None
            and input_tokens is not None
            and output_tokens is not None
        ):
            self.record_llm_call(
                job_id=job_id,
                agent_name=agent_name,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        data: dict[str, Any] = {
            "agent_name": agent_name,
            "model_id": model_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_cents": estimated_cost_cents,
            "duration_ms": duration_ms,
        }

        if settings.debug_log_responses:
            data["response_text"] = self._truncate(response_text)
            # Serialize parsed output if it's a Pydantic model
            if parsed_output is not None:
                if hasattr(parsed_output, "model_dump"):
                    data["parsed_output"] = self._truncate(
                        json.dumps(parsed_output.model_dump(), default=str)
                    )
                elif hasattr(parsed_output, "dict"):
                    data["parsed_output"] = self._truncate(
                        json.dumps(parsed_output.dict(), default=str)
                    )
                else:
                    data["parsed_output"] = self._truncate(str(parsed_output))
        else:
            data["response_text_length"] = len(response_text) if response_text else 0

        self._log(DebugEventType.RESPONSE_RECEIVED, job_id, data)

    def log_phase_start(
        self,
        job_id: str,
        phase_name: str,
        phase_number: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log the start of a processing phase.

        Args:
            job_id: Job ID for correlation
            phase_name: Name of the phase (e.g., "analysis", "extraction")
            phase_number: Phase number (1-4)
            details: Additional phase-specific details
        """
        self._log(
            DebugEventType.PHASE_START,
            job_id,
            {
                "phase_name": phase_name,
                "phase_number": phase_number,
                **(details or {}),
            },
        )

    def log_phase_end(
        self,
        job_id: str,
        phase_name: str,
        phase_number: int | None = None,
        duration_ms: float | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> None:
        """Log the end of a processing phase.

        Args:
            job_id: Job ID for correlation
            phase_name: Name of the phase
            phase_number: Phase number (1-4)
            duration_ms: Phase duration in milliseconds
            result_summary: Summary of phase results
        """
        self._log(
            DebugEventType.PHASE_END,
            job_id,
            {
                "phase_name": phase_name,
                "phase_number": phase_number,
                "duration_ms": duration_ms,
                "result_summary": result_summary,
            },
        )

    def log_phase_error(
        self,
        job_id: str,
        phase_name: str,
        error: str,
        error_type: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a phase error.

        Args:
            job_id: Job ID for correlation
            phase_name: Name of the phase
            error: Error message
            error_type: Type of error
            details: Additional error details
        """
        self._log(
            DebugEventType.PHASE_ERROR,
            job_id,
            {
                "phase_name": phase_name,
                "error": error,
                "error_type": error_type,
                **(details or {}),
            },
        )

    def log_system_event(
        self,
        event_name: str,
        job_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a general system event.

        Args:
            event_name: Name of the event
            job_id: Job ID if applicable
            details: Event-specific details
        """
        self._log(
            DebugEventType.SYSTEM_EVENT,
            job_id,
            {
                "event_name": event_name,
                **(details or {}),
            },
        )

    def log_job_queued(
        self,
        job_id: str,
        queue_name: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Log a job being queued.

        Args:
            job_id: Job ID
            queue_name: Name of the queue
            payload: Queue payload
        """
        self._log(
            DebugEventType.JOB_QUEUED,
            job_id,
            {
                "queue_name": queue_name,
                "payload": payload,
            },
        )

    def log_job_started(
        self,
        job_id: str,
        worker_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a job starting processing.

        Args:
            job_id: Job ID
            worker_type: Type of worker processing the job
            details: Additional details
        """
        self._log(
            DebugEventType.JOB_STARTED,
            job_id,
            {
                "worker_type": worker_type,
                **(details or {}),
            },
        )

    def log_job_completed(
        self,
        job_id: str,
        worker_type: str,
        duration_ms: float | None = None,
        result_summary: dict[str, Any] | None = None,
    ) -> None:
        """Log a job completing successfully.

        Args:
            job_id: Job ID
            worker_type: Type of worker that processed the job
            duration_ms: Total processing duration
            result_summary: Summary of results
        """
        self._log(
            DebugEventType.JOB_COMPLETED,
            job_id,
            {
                "worker_type": worker_type,
                "duration_ms": duration_ms,
                "result_summary": result_summary,
            },
        )

    def log_job_failed(
        self,
        job_id: str,
        worker_type: str,
        error: str,
        error_type: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        """Log a job failing.

        Args:
            job_id: Job ID
            worker_type: Type of worker that processed the job
            error: Error message
            error_type: Type of error
            duration_ms: Duration before failure
        """
        self._log(
            DebugEventType.JOB_FAILED,
            job_id,
            {
                "worker_type": worker_type,
                "error": error,
                "error_type": error_type,
                "duration_ms": duration_ms,
            },
        )

    def log_worker_event(
        self,
        event: DebugEventType,
        worker_type: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a worker lifecycle event.

        Args:
            event: Worker event type
            worker_type: Type of worker
            details: Additional details
        """
        self._log(
            event,
            None,
            {
                "worker_type": worker_type,
                **(details or {}),
            },
        )

    # =========================================================================
    # Latency Tracking Methods
    # =========================================================================

    def start_job_tracking(self, job_id: str) -> JobLatencyTracker | None:
        """Start latency tracking for a job.

        Creates a new JobLatencyTracker if debug mode is enabled.

        Args:
            job_id: Job ID to track

        Returns:
            JobLatencyTracker if debug mode enabled, None otherwise
        """
        if not self.enabled:
            return None
        return create_job_tracker(job_id)

    def start_phase_tracking(
        self,
        job_id: str,
        phase_name: str,
        phase_number: int,
    ) -> None:
        """Start tracking a processing phase.

        Args:
            job_id: Job ID
            phase_name: Name of the phase
            phase_number: Phase number (1-4)
        """
        if not self.enabled:
            return

        tracker = get_job_tracker(job_id)
        if tracker:
            tracker.start_phase(phase_name, phase_number)

    def end_phase_tracking(self, job_id: str) -> PhaseLatencyMetrics | None:
        """End tracking for the current phase.

        Args:
            job_id: Job ID

        Returns:
            PhaseLatencyMetrics for the completed phase, or None
        """
        if not self.enabled:
            return None

        tracker = get_job_tracker(job_id)
        if tracker:
            return tracker.end_phase()
        return None

    def record_llm_call(
        self,
        job_id: str,
        agent_name: str,
        duration_ms: float,
        input_tokens: int,
        output_tokens: int,
    ) -> LLMLatencyMetrics | None:
        """Record an LLM call within the current phase.

        Args:
            job_id: Job ID
            agent_name: Name of the agent that made the call
            duration_ms: Call duration in milliseconds
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            LLMLatencyMetrics for the call, or None if tracking disabled
        """
        if not self.enabled:
            return None

        tracker = get_job_tracker(job_id)
        if tracker:
            return tracker.record_llm_call(
                agent_name=agent_name,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        return None

    def finish_job_tracking(self, job_id: str) -> dict[str, Any] | None:
        """Finish tracking and get the latency summary.

        This marks the job as complete, retrieves the summary,
        and removes the tracker from the registry.

        Args:
            job_id: Job ID

        Returns:
            Latency summary dict, or None if tracking disabled
        """
        if not self.enabled:
            return None

        tracker = remove_job_tracker(job_id)
        if tracker:
            tracker.finish()
            return tracker.get_summary()
        return None

    def log_latency_summary(
        self,
        job_id: str,
        summary: dict[str, Any] | None = None,
    ) -> None:
        """Log the latency summary for a completed job.

        Args:
            job_id: Job ID
            summary: Pre-computed summary (if None, will finish and get summary)
        """
        if not self.enabled:
            return

        if summary is None:
            summary = self.finish_job_tracking(job_id)

        if summary:
            self._log(
                DebugEventType.JOB_COMPLETED,
                job_id,
                {
                    "latency_summary": summary,
                    "timing_total_seconds": summary.get("timing", {}).get(
                        "total_duration_seconds"
                    ),
                    "llm_efficiency_pct": summary.get("timing", {}).get(
                        "llm_efficiency_pct"
                    ),
                    "total_llm_calls": summary.get("throughput", {}).get(
                        "total_llm_calls"
                    ),
                    "avg_tokens_per_second": summary.get("throughput", {}).get(
                        "avg_output_tokens_per_second"
                    ),
                },
            )


# Global singleton instance
debug_logger = DebugLoggingService()
