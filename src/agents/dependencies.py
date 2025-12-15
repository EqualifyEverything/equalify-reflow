"""Runtime dependencies for agent dynamic instructions.

This module provides the AgentDependencies dataclass that enables dynamic
instruction generation based on runtime context:

- Document manifest from Analysis phase
- Failure history for retry guidance
- Document type for specialized hints
- Custom context for agent-specific data

Example:
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
    >>>     if ctx.deps.is_retry:
    >>>         failures = "\\n".join(f"- {f}" for f in ctx.deps.previous_failures[-3:])
    >>>         return f"Previous attempts failed on:\\n{failures}\\nTry alternative approaches."
    >>>     return ""
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.services.job_service import JobService
    from src.shared.models.remediation import DocumentManifest


@dataclass
class AgentDependencies:
    """Runtime dependencies injected into agent instructions.

    Provides context that allows dynamic instruction generation
    based on job state, document analysis, and failure history.

    This dataclass is passed to PydanticAI agents via `deps=` parameter,
    and accessed in `@agent.instructions` functions via `ctx.deps`.

    Attributes:
        job_id: Current job identifier (required)
        job_service: Job service for state queries (optional, for advanced use)
        manifest: Document manifest from analysis phase (for extraction/specialized agents)
        previous_failures: List of error messages from previous attempts (for retry guidance)
        attempt_number: Current attempt number (1 = first try, 2+ = retry)
        document_type: Document type from analysis (syllabus, exam, etc.)
        custom_context: Additional context for specialized use cases

    Example:
        >>> deps = AgentDependencies(job_id="job-123")
        >>> deps.manifest = manifest_from_analysis
        >>> deps.document_type = "syllabus"
        >>>
        >>> # Track failures for retry guidance
        >>> deps.add_failure("Failed to parse table on page 3")
        >>> print(deps.is_retry)  # True
        >>> print(deps.attempt_number)  # 2
    """

    job_id: str
    """Current job identifier."""

    job_service: JobService | None = None
    """Job service for state queries (optional, for advanced use)."""

    manifest: DocumentManifest | None = None
    """Document manifest from analysis phase (for extraction/specialized agents)."""

    previous_failures: list[str] = field(default_factory=list)
    """List of error messages from previous attempts (for retry guidance)."""

    attempt_number: int = 1
    """Current attempt number (1 = first try, 2+ = retry)."""

    document_type: str | None = None
    """Document type from analysis (syllabus, exam, lecture_notes, etc.)."""

    custom_context: dict[str, Any] = field(default_factory=dict)
    """Additional context for specialized use cases.

    Common keys used by agents:
    - 'current_page_features': Dict with page_num, image_count, layout_type
    - 'current_page_num': int for the page being processed
    """

    def add_failure(self, error_message: str) -> None:
        """Record a failure for retry guidance.

        Automatically increments attempt_number when called.

        Args:
            error_message: Description of what failed
        """
        self.previous_failures.append(error_message)
        self.attempt_number += 1

    def clear_failures(self) -> None:
        """Clear recorded failures and reset attempt number."""
        self.previous_failures.clear()
        self.attempt_number = 1

    @property
    def is_retry(self) -> bool:
        """Check if this is a retry attempt.

        Returns:
            True if attempt_number > 1
        """
        return self.attempt_number > 1

    def clone_for_page(self, page_num: int, **page_context: Any) -> AgentDependencies:
        """Create a copy with page-specific context.

        Useful when processing pages in a loop - creates a fresh deps
        instance with the current page's context without modifying the original.

        Args:
            page_num: Page number being processed
            **page_context: Additional page-specific context (image_count, layout_type, etc.)

        Returns:
            New AgentDependencies with updated custom_context

        Example:
            >>> base_deps = AgentDependencies(job_id="123", manifest=manifest)
            >>> for page in pages:
            ...     page_deps = base_deps.clone_for_page(
            ...         page.page_num,
            ...         image_count=features.image_count,
            ...         layout_type=features.layout_type,
            ...     )
            ...     result = await agent.run(message, deps=page_deps)
        """
        return AgentDependencies(
            job_id=self.job_id,
            job_service=self.job_service,
            manifest=self.manifest,
            previous_failures=list(self.previous_failures),  # Copy list
            attempt_number=self.attempt_number,
            document_type=self.document_type,
            custom_context={
                **self.custom_context,
                "current_page_num": page_num,
                "current_page_features": page_context,
            },
        )


__all__ = ["AgentDependencies"]
