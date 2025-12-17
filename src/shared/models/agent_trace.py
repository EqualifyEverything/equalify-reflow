"""Agent trace models for glass box transparency.

AgentTrace captures everything a specialized agent did during processing,
including what it observed, what it decided, and its reasoning.

AgentResult is the standard output format for all specialized agents,
which gets converted to AgentTrace with timing/cost metadata.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .auto_correction import AutoCorrection
from .observation import Observation
from .review_checklist import ReviewItem


class AgentTrace(BaseModel):
    """What one agent did - full transparency.

    AgentTrace is the glass box record of a specialized agent's execution.
    It captures observations, decisions, and reasoning for auditability.

    This model is included in ProcessingTrace to provide complete
    visibility into the pipeline execution.

    Attributes:
        agent_name: Which agent executed (figures, tables, structure, typography)
        observations: Issues the agent detected
        auto_corrections: Safe corrections applied automatically
        review_items: Items requiring human decision
        reasoning_summary: Human-readable summary of what agent did
        confidence: Overall confidence in agent's output
        cost_cents: LLM cost for this agent's execution
        time_seconds: Execution time
        iterations: Number of validation loop iterations (if applicable)
        started_at: When agent started
        completed_at: When agent finished

    Example:
        >>> trace = AgentTrace(
        ...     agent_name="figures",
        ...     observations=[obs1, obs2],
        ...     auto_corrections=[correction1],
        ...     review_items=[item1],
        ...     reasoning_summary="Processed 3 images. 2 auto-corrected, 1 needs review.",
        ...     confidence=0.88,
        ...     cost_cents=12.5,
        ...     time_seconds=25.0,
        ...     started_at=datetime.now(UTC),
        ...     completed_at=datetime.now(UTC)
        ... )
    """

    agent_name: Literal["figures", "tables", "structure", "typography"] = Field(
        ...,
        description="Which agent executed"
    )

    # What it saw
    observations: list[Observation] = Field(
        default_factory=list,
        description="Issues the agent detected"
    )

    # What it decided
    auto_corrections: list[AutoCorrection] = Field(
        default_factory=list,
        description="Safe corrections applied automatically"
    )
    review_items: list[ReviewItem] = Field(
        default_factory=list,
        description="Items requiring human decision"
    )

    # Its reasoning (glass box)
    reasoning_summary: str = Field(
        ...,
        description="Human-readable summary of what agent did"
    )

    # Metrics
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in agent's output"
    )
    cost_cents: float = Field(
        default=0.0,
        ge=0.0,
        description="LLM cost for this agent's execution"
    )
    time_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Execution time in seconds"
    )
    iterations: int = Field(
        default=1,
        ge=1,
        description="Number of validation loop iterations"
    )

    # Tracking
    started_at: datetime = Field(
        ...,
        description="When agent started"
    )
    completed_at: datetime = Field(
        ...,
        description="When agent finished"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_name": "figures",
                "observations": [],
                "auto_corrections": [],
                "review_items": [],
                "reasoning_summary": "Processed 3 images. 2 auto-corrected, 1 needs review.",
                "confidence": 0.88,
                "cost_cents": 12.5,
                "time_seconds": 25.0,
                "iterations": 1,
                "started_at": "2024-12-10T10:30:00Z",
                "completed_at": "2024-12-10T10:30:25Z"
            }
        }
    )


class AgentResult(BaseModel):
    """Standard output format for all specialized agents.

    This is the return type from specialized agent functions. It contains
    the raw output which is then converted to AgentTrace with timing
    and cost metadata by the orchestrator.

    Attributes:
        agent_name: Which agent produced this result
        observations: Issues detected
        auto_corrections: Safe corrections to apply
        review_items: Items needing human review
        reasoning_summary: Human-readable summary
        confidence: Overall confidence
        enhanced_content: Optional placeholder replacements (placeholder_id -> content)
        cost_cents: LLM cost (added by agent)
        time_seconds: Execution time (added by agent)
        iterations: Validation loop iterations

    Example:
        >>> result = AgentResult(
        ...     agent_name="figures",
        ...     observations=[obs],
        ...     auto_corrections=[],
        ...     review_items=[item],
        ...     reasoning_summary="Found 1 image with missing alt text",
        ...     confidence=0.85,
        ...     enhanced_content={"fig-1": "![Chart showing growth](fig-1.png)"}
        ... )
    """

    agent_name: str = Field(
        ...,
        description="Which agent produced this result"
    )
    observations: list[Observation] = Field(
        default_factory=list,
        description="Issues detected"
    )
    auto_corrections: list[AutoCorrection] = Field(
        default_factory=list,
        description="Safe corrections to apply"
    )
    review_items: list[ReviewItem] = Field(
        default_factory=list,
        description="Items needing human review"
    )
    reasoning_summary: str = Field(
        ...,
        description="Human-readable summary"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence"
    )
    enhanced_content: dict[str, str] | None = Field(
        default=None,
        description="placeholder_id -> enhanced content"
    )

    # Metrics (for conversion to AgentTrace)
    cost_cents: float = Field(
        default=0.0,
        ge=0.0,
        description="LLM cost"
    )
    time_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Execution time"
    )
    iterations: int = Field(
        default=1,
        ge=1,
        description="Validation loop iterations"
    )

    def to_trace(self, started_at: datetime, completed_at: datetime) -> AgentTrace:
        """Convert AgentResult to AgentTrace with timing info.

        Args:
            started_at: When agent started execution
            completed_at: When agent finished execution

        Returns:
            AgentTrace with full execution metadata

        Raises:
            ValueError: If agent_name is not a valid agent type
        """
        # Validate agent_name is a valid literal
        valid_agents = {"figures", "tables", "structure", "typography"}
        if self.agent_name not in valid_agents:
            raise ValueError(f"Invalid agent_name: {self.agent_name}. Must be one of {valid_agents}")

        return AgentTrace(
            agent_name=self.agent_name,  # type: ignore[arg-type]
            observations=self.observations,
            auto_corrections=self.auto_corrections,
            review_items=self.review_items,
            reasoning_summary=self.reasoning_summary,
            confidence=self.confidence,
            cost_cents=self.cost_cents,
            time_seconds=self.time_seconds,
            iterations=self.iterations,
            started_at=started_at,
            completed_at=completed_at,
        )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "agent_name": "figures",
                "observations": [],
                "auto_corrections": [],
                "review_items": [],
                "reasoning_summary": "Found 1 image with missing alt text",
                "confidence": 0.85,
                "enhanced_content": {"fig-1": "![Chart showing growth](fig-1.png)"},
                "cost_cents": 5.0,
                "time_seconds": 10.0,
                "iterations": 1
            }
        }
    )


__all__ = ["AgentTrace", "AgentResult"]
