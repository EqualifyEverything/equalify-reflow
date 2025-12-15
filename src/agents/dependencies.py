"""Agent dependencies for dynamic instruction injection.

This module provides a dataclass for runtime dependencies that can be
passed to PydanticAI agents via the deps_type parameter. This enables
dynamic instruction injection using the @agent.instructions decorator.

Future use case example:
    >>> from pydantic_ai import RunContext
    >>>
    >>> @extraction_agent.instructions
    >>> async def manifest_guidance(ctx: RunContext[AgentDependencies]) -> str:
    >>>     if ctx.deps.manifest:
    >>>         sections = "\\n".join(
    >>>             f"- {h.title} (H{h.level})"
    >>>             for h in ctx.deps.manifest.heading_tree.nodes
    >>>         )
    >>>         return f"Follow this document structure:\\n{sections}"
    >>>     return ""
    >>>
    >>> @extraction_agent.instructions
    >>> async def retry_guidance(ctx: RunContext[AgentDependencies]) -> str:
    >>>     if ctx.deps.previous_failures:
    >>>         failures = "\\n".join(f"- {f}" for f in ctx.deps.previous_failures)
    >>>         return f"Previous attempts failed on:\\n{failures}\\nTry alternative approaches."
    >>>     return ""
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.job_service import JobService
    from src.shared.models.remediation import DocumentManifest


@dataclass
class AgentDependencies:
    """Runtime dependencies for agent instructions.

    This dataclass holds context that can be used by @agent.instructions
    decorators to dynamically modify agent behavior at runtime.

    Attributes:
        job_id: Current job identifier for logging and state tracking
        job_service: Service for accessing job state (optional, for advanced use)
        manifest: Document manifest from analysis phase (for extraction context)
        previous_failures: List of previous failure descriptions (for retry guidance)

    Example:
        >>> deps = AgentDependencies(
        ...     job_id="job-123",
        ...     job_service=job_service,
        ...     manifest=manifest,
        ...     previous_failures=["Failed to parse table on page 3"],
        ... )
        >>> result = await agent.run(message, deps=deps)
    """

    job_id: str
    job_service: JobService | None = None
    manifest: DocumentManifest | None = None
    previous_failures: list[str] = field(default_factory=list)

    def add_failure(self, failure: str) -> None:
        """Record a failure for retry guidance.

        Args:
            failure: Description of what failed
        """
        self.previous_failures.append(failure)

    def clear_failures(self) -> None:
        """Clear recorded failures (e.g., after successful retry)."""
        self.previous_failures.clear()


__all__ = ["AgentDependencies"]
