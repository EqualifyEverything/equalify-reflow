"""Progress tracking models for the remediation pipeline.

These models track fine-grained progress within the "processing" job status,
allowing the UI to show detailed status updates during the remediation pipeline.
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Valid substatuses within the "processing" status
VALID_SUBSTATUSES: dict[str, list[str]] = {
    "processing": [
        "analyzing",        # Phase 1: Sonnet analysis
        "extracting",       # Phase 2: Haiku extraction
        "specializing",     # Phase 3: Specialized agents
        "consolidating",    # Phase 4: Observations → Proposals
        "awaiting_review",  # Phase 5: Human review
        "applying",         # Phase 6: Applying approved edits
    ]
}

# Type alias for substatus literals
SubstatusType = Literal[
    "analyzing",
    "extracting",
    "specializing",
    "consolidating",
    "awaiting_review",
    "applying",
]


class RemediationProgress(BaseModel):
    """Progress tracking for remediation pipeline.

    Provides fine-grained progress information within the "processing" status.
    Used by the job service to track pipeline state and by the UI to show
    detailed progress to users.

    Attributes:
        substatus: Current phase within processing
        observation_count: Total observations generated
        proposal_count: Total proposals generated
        pending_proposals: Proposals awaiting review
        approved_proposals: Proposals approved by reviewer
        rejected_proposals: Proposals rejected by reviewer
        applied_proposals: Proposals successfully applied
        manual_observations: Observations requiring manual attention
        analysis_model: Model used for analysis phase
        extraction_model: Model used for extraction phase
        analysis_completed_at: When analysis phase completed
        extraction_completed_at: When extraction phase completed
        review_started_at: When human review began

    Example:
        >>> progress = RemediationProgress(
        ...     substatus="awaiting_review",
        ...     observation_count=12,
        ...     proposal_count=8,
        ...     pending_proposals=6,
        ...     manual_observations=2
        ... )
    """

    substatus: SubstatusType = Field(
        default="analyzing",
        description="Current phase within processing"
    )

    # Counts
    observation_count: int = Field(
        default=0,
        ge=0,
        description="Total observations generated"
    )
    proposal_count: int = Field(
        default=0,
        ge=0,
        description="Total proposals generated"
    )
    pending_proposals: int = Field(
        default=0,
        ge=0,
        description="Proposals awaiting review"
    )
    approved_proposals: int = Field(
        default=0,
        ge=0,
        description="Proposals approved by reviewer"
    )
    rejected_proposals: int = Field(
        default=0,
        ge=0,
        description="Proposals rejected by reviewer"
    )
    applied_proposals: int = Field(
        default=0,
        ge=0,
        description="Proposals successfully applied"
    )
    manual_observations: int = Field(
        default=0,
        ge=0,
        description="Observations requiring manual attention"
    )

    # Model tracking
    analysis_model: str | None = Field(
        default=None,
        description="Model used for analysis phase (e.g., claude-sonnet-4-5)"
    )
    extraction_model: str | None = Field(
        default=None,
        description="Model used for extraction phase (e.g., claude-haiku-4-5)"
    )

    # Timestamps
    analysis_completed_at: datetime | None = Field(
        default=None,
        description="When analysis phase completed"
    )
    extraction_completed_at: datetime | None = Field(
        default=None,
        description="When extraction phase completed"
    )
    review_started_at: datetime | None = Field(
        default=None,
        description="When human review began"
    )

    def is_valid_substatus_transition(
        self,
        current_status: str,
        new_substatus: str
    ) -> bool:
        """Check if substatus is valid for given job status.

        Args:
            current_status: Current job status (e.g., "processing")
            new_substatus: Substatus to validate

        Returns:
            True if substatus is valid for the job status
        """
        valid_substatuses = VALID_SUBSTATUSES.get(current_status, [])
        return new_substatus in valid_substatuses

    def update_counts_from_lists(
        self,
        observations: list[Any],
        proposals: list[Any],
    ) -> "RemediationProgress":
        """Update counts based on observation and proposal lists.

        Convenience method to recalculate all counts from the current
        state of observations and proposals.

        Args:
            observations: List of Observation objects
            proposals: List of Proposal objects

        Returns:
            Self with updated counts
        """
        self.observation_count = len(observations)
        self.manual_observations = sum(
            1 for o in observations if getattr(o, "route", None) == "manual"
        )

        self.proposal_count = len(proposals)
        self.pending_proposals = sum(
            1 for p in proposals if getattr(p, "status", None) == "pending"
        )
        self.approved_proposals = sum(
            1 for p in proposals if getattr(p, "status", None) == "approved"
        )
        self.rejected_proposals = sum(
            1 for p in proposals if getattr(p, "status", None) == "rejected"
        )
        self.applied_proposals = sum(
            1 for p in proposals if getattr(p, "status", None) == "applied"
        )

        return self

    def mark_analysis_complete(self, model: str) -> "RemediationProgress":
        """Mark analysis phase as complete.

        Args:
            model: Model used for analysis

        Returns:
            Self with updated fields
        """
        self.substatus = "extracting"
        self.analysis_model = model
        self.analysis_completed_at = datetime.now(UTC)
        return self

    def mark_extraction_complete(self, model: str) -> "RemediationProgress":
        """Mark extraction phase as complete.

        Args:
            model: Model used for extraction

        Returns:
            Self with updated fields
        """
        self.substatus = "specializing"
        self.extraction_model = model
        self.extraction_completed_at = datetime.now(UTC)
        return self

    def mark_ready_for_review(self) -> "RemediationProgress":
        """Mark pipeline as ready for human review.

        Returns:
            Self with updated fields
        """
        self.substatus = "awaiting_review"
        self.review_started_at = datetime.now(UTC)
        return self

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "substatus": "awaiting_review",
                "observation_count": 12,
                "proposal_count": 8,
                "pending_proposals": 6,
                "approved_proposals": 0,
                "rejected_proposals": 0,
                "applied_proposals": 0,
                "manual_observations": 2,
                "analysis_model": "claude-sonnet-4-5",
                "extraction_model": "claude-haiku-4-5",
                "analysis_completed_at": "2024-12-10T10:32:00Z",
                "extraction_completed_at": "2024-12-10T10:34:00Z",
                "review_started_at": "2024-12-10T10:35:00Z"
            }
        }
    )
