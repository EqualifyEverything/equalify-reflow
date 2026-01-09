"""Subagent tools for domain agents.

Each subagent is a specialized LLM that returns structured recommendations
with confidence scores. Parent agents review recommendations and decide
whether to apply edits via propose_edit().
"""

from src.utils.circuit_breaker import CircuitBreaker

from .citations import CITATION_SYSTEM_PROMPT, invoke_citation_subagent
from .footnotes import FOOTNOTE_SYSTEM_PROMPT, invoke_footnote_subagent
from .lists import LIST_SEMANTICS_SYSTEM_PROMPT, invoke_list_subagent
from .page_artifacts import PAGE_ARTIFACT_SYSTEM_PROMPT, invoke_page_artifact_subagent
from .paragraph_merge import (
    PARAGRAPH_MERGE_SYSTEM_PROMPT,
    invoke_paragraph_merge_subagent,
)
from .types import SubagentResult
from .typography import (
    MAX_BATCH_SIZE,
    TYPOGRAPHY_BATCH_SYSTEM_PROMPT,
    TYPOGRAPHY_SYSTEM_PROMPT,
    invoke_typography_subagent,
    invoke_typography_subagent_batch,
    reset_typography_agents,
)

# =============================================================================
# Shared LLM Circuit Breaker
# =============================================================================

# LLM-specific circuit breaker with tuned parameters for Bedrock
# - Lower failure threshold (3 vs 5 for S3) since LLM failures waste tokens
# - Longer timeout (120s) to allow Bedrock recovery from throttling
# - Single test call in half-open state to avoid wasting tokens during recovery
llm_circuit_breaker = CircuitBreaker(
    name="bedrock-llm",
    failure_threshold=3,    # Lower than S3 (which uses 5)
    success_threshold=2,    # 2 successes to close
    timeout=120.0,          # Longer recovery for LLM
)


def is_rate_limit_error(exception: Exception) -> bool:
    """Check if an exception is a rate limit error that shouldn't count as failure.

    Rate limit errors (429) are transient and shouldn't trigger circuit breaker
    opening - they indicate the service is working but we're calling too fast.

    Args:
        exception: The exception to check

    Returns:
        True if this is a rate limit error, False otherwise
    """
    error_str = str(exception).lower()

    # Check for common rate limit indicators
    rate_limit_indicators = [
        "throttling",
        "rate limit",
        "ratelimit",
        "429",
        "too many requests",
        "request limit exceeded",
        "throttled",
    ]

    return any(indicator in error_str for indicator in rate_limit_indicators)


def reset_llm_circuit_breaker() -> None:
    """Reset the LLM circuit breaker to closed state.

    Useful for testing or manual recovery.
    """
    llm_circuit_breaker.reset()

# =============================================================================
# Confidence Thresholds
# =============================================================================

CONFIDENCE_AUTO_APPLY = 0.8  # Apply automatically
CONFIDENCE_APPLY_WITH_REVIEW = 0.5  # Apply but flag for review
CONFIDENCE_SKIP = 0.5  # Below this, skip the edit


__all__ = [
    # Constants
    "CONFIDENCE_AUTO_APPLY",
    "CONFIDENCE_APPLY_WITH_REVIEW",
    "CONFIDENCE_SKIP",
    "MAX_BATCH_SIZE",
    # Circuit breaker
    "llm_circuit_breaker",
    "is_rate_limit_error",
    "reset_llm_circuit_breaker",
    # Base type
    "SubagentResult",
    # Subagent invoke functions
    "invoke_page_artifact_subagent",
    "invoke_footnote_subagent",
    "invoke_citation_subagent",
    "invoke_list_subagent",
    "invoke_typography_subagent",
    "invoke_typography_subagent_batch",
    "invoke_paragraph_merge_subagent",
    # System prompts (for testing/inspection)
    "PAGE_ARTIFACT_SYSTEM_PROMPT",
    "FOOTNOTE_SYSTEM_PROMPT",
    "CITATION_SYSTEM_PROMPT",
    "LIST_SEMANTICS_SYSTEM_PROMPT",
    "TYPOGRAPHY_SYSTEM_PROMPT",
    "TYPOGRAPHY_BATCH_SYSTEM_PROMPT",
    "PARAGRAPH_MERGE_SYSTEM_PROMPT",
    # Agent reset functions (for testing)
    "reset_typography_agents",
]
