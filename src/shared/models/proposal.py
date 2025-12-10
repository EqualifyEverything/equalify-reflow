"""Proposal models for the remediation pipeline.

Proposals are suggested edits that resolve one or more observations.
They contain search-replace diffs and require human approval before application.
"""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# Valid proposal status transitions
VALID_PROPOSAL_TRANSITIONS: dict[str, list[str]] = {
    "pending": ["approved", "rejected"],
    "approved": ["applied", "failed"],
    "rejected": [],      # Terminal state
    "applied": [],       # Terminal state
    "failed": ["pending"],  # Can retry after failure
}


class SearchReplaceDiff(BaseModel):
    """A single search-replace edit operation.

    Represents an atomic edit to be applied to the markdown document.
    The search text must be found exactly in the document for the
    replacement to succeed.

    Attributes:
        search: Exact text to find in document (must be unique)
        replace: Text to substitute (can be empty for deletion)

    Example:
        >>> diff = SearchReplaceDiff(
        ...     search="![](images/figure-1.png)",
        ...     replace="![Flowchart showing registration process](images/figure-1.png)"
        ... )
    """

    search: str = Field(
        ...,
        min_length=1,
        description="Exact text to find in document"
    )
    replace: str = Field(
        ...,
        description="Text to substitute (can be empty for deletion)"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "search": "![](images/figure-1.png)",
                "replace": "![Flowchart showing course prerequisites](images/figure-1.png)"
            }
        }
    )


class Proposal(BaseModel):
    """A suggested edit that resolves one or more observations.

    Proposals are the unit of human review. Each proposal:
    - Resolves one or more observations
    - Contains a search-replace diff
    - Includes justification for the change
    - Requires human approval before application

    Status Transitions:
        - pending → approved | rejected
        - approved → applied | failed
        - failed → pending (can retry)
        - rejected (terminal)
        - applied (terminal)

    Attributes:
        id: UUID for this proposal
        job_id: Associated job ID
        resolves: List of observation IDs this proposal resolves
        diff: The search-replace edit operation
        justification: Why these observations are grouped and how edit addresses them
        page_nums: Pages affected by this proposal
        estimated_impact: Human-readable impact summary
        route: Whether this can be auto-approved or needs manual review
        status: Current lifecycle status
        failure_reason: Why application failed (if status=failed)
        reviewed_by: Who reviewed this proposal
        reviewed_at: When review occurred
        review_notes: Notes from reviewer
        created_at: When proposal was created

    Example:
        >>> proposal = Proposal(
        ...     id="proposal-uuid",
        ...     job_id="job-uuid",
        ...     resolves=["obs-1", "obs-2"],
        ...     diff=SearchReplaceDiff(
        ...         search="![](figure-1.png)",
        ...         replace="![Course prerequisite flowchart](figure-1.png)"
        ...     ),
        ...     justification="Combining empty alt text observation with visual content analysis.",
        ...     page_nums=[3],
        ...     estimated_impact="Adds alt text to 1 image"
        ... )
    """

    id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="UUID for this proposal"
    )
    job_id: str = Field(..., description="Associated job ID")

    # What this addresses
    resolves: list[str] = Field(
        ...,
        min_length=1,
        description="Observation IDs this proposal resolves"
    )

    # The edit
    diff: SearchReplaceDiff

    # Justification (critical for human review)
    justification: str = Field(
        ...,
        min_length=1,
        description="Why these observations are grouped and how the edit addresses them"
    )

    # Context
    page_nums: list[int] = Field(
        default_factory=list,
        description="Pages affected by this proposal"
    )
    estimated_impact: str = Field(
        default="",
        description="Human-readable impact summary"
    )

    # Routing
    route: Literal["auto", "manual"] = Field(
        default="auto",
        description="Whether this can be auto-approved or needs manual review"
    )

    # Lifecycle
    status: Literal["pending", "approved", "rejected", "applied", "failed"] = Field(
        default="pending",
        description="Current lifecycle status"
    )
    failure_reason: str | None = Field(
        default=None,
        description="Why application failed (if status=failed)"
    )

    # Review
    reviewed_by: str | None = Field(
        default=None,
        description="Who reviewed this proposal"
    )
    reviewed_at: datetime | None = Field(
        default=None,
        description="When review occurred"
    )
    review_notes: str | None = Field(
        default=None,
        description="Notes from reviewer"
    )

    # Metadata
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def can_transition_to(self, new_status: str) -> bool:
        """Check if transition to new status is valid.

        Args:
            new_status: Target status to transition to

        Returns:
            True if transition is allowed by state machine
        """
        valid_next = VALID_PROPOSAL_TRANSITIONS.get(self.status, [])
        return new_status in valid_next

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440001",
                "job_id": "550e8400-e29b-41d4-a716-446655440000",
                "resolves": ["obs-1", "obs-2"],
                "diff": {
                    "search": "![](images/figure-1.png)",
                    "replace": "![Flowchart showing CS 101 as prerequisite for CS 201](images/figure-1.png)"
                },
                "justification": "Combining empty alt text observation with visual content analysis.",
                "page_nums": [3],
                "estimated_impact": "Adds descriptive alt text to 1 image",
                "route": "auto",
                "status": "pending",
                "failure_reason": None,
                "reviewed_by": None,
                "reviewed_at": None,
                "review_notes": None,
                "created_at": "2024-12-10T10:35:00Z"
            }
        }
    )
