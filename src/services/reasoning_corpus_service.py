"""Reasoning corpus service for structured logging of agent reasoning.

This service provides structured logging of reasoning from Reasoned[T] fields,
enabling analysis of model reasoning patterns over time.

The corpus can be used to:
1. Analyze common reasoning patterns
2. Identify areas where models are uncertain
3. Train/fine-tune future models on reasoning examples
4. Debug specific job failures

Currently uses structured logging; S3 archival can be added later.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


class ReasoningCorpusService:
    """Service for storing and analyzing agent reasoning.

    Logs reasoning entries from Reasoned[T] fields in a structured format
    for later analysis. Each entry captures:
    - Job context (job_id, agent, model class)
    - Field being reasoned about
    - The reasoning text and resulting value
    - Metadata (length, sentence count, timestamp)

    SECURITY CONSIDERATIONS
    =======================

    The reasoning corpus contains LLM-generated explanations that may include:
    - Direct quotes from document content
    - Paraphrased sensitive information
    - Model hallucinations that could appear factual

    Access Controls Required:
        1. Reasoning logs should be stored in a separate, restricted location
        2. Access should be limited to:
           - System administrators for debugging
           - ML engineers for model improvement
           - Auditors for compliance review
        3. Reasoning should NOT be:
           - Exposed to end users without review
           - Included in API responses
           - Logged to general application logs

    PII Handling:
        - Consider running PII detection on reasoning text before storage
        - Implement retention policies (e.g., 90-day auto-delete)
        - Anonymize or redact document identifiers in exported data

    Threat Model:
        - Reasoning may inadvertently reproduce PII from documents
        - Model hallucinations could generate false but sensitive-looking data
        - Log aggregators could expose reasoning to unauthorized parties

    Example:
        >>> corpus_service = ReasoningCorpusService()
        >>> await corpus_service.log_reasoning(
        ...     job_id="job-123",
        ...     agent_name="figures",
        ...     model_class="ImageAnalysis",
        ...     field="image_type",
        ...     reasoning="Large chart with data points and axis labels visible.",
        ...     value="informative",
        ... )
    """

    def __init__(self) -> None:
        """Initialize the reasoning corpus service."""
        self.entry_count = 0

    async def log_reasoning(
        self,
        job_id: str,
        agent_name: str,
        model_class: str,
        field: str,
        reasoning: str,
        value: Any,
    ) -> None:
        """Log a single reasoning entry via structured logging.

        Args:
            job_id: Job identifier
            agent_name: Name of the agent that produced this reasoning
            model_class: Pydantic model class name (e.g., "ImageAnalysis")
            field: Field name that was reasoned about
            reasoning: The reasoning text produced by the model
            value: The value determined by the reasoning
        """
        # Count approximate sentences
        sentence_count = reasoning.count(". ") + reasoning.count("! ") + reasoning.count("? ") + 1

        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "job_id": job_id,
            "agent": agent_name,
            "model_class": model_class,
            "field": field,
            "reasoning": reasoning,
            "value": str(value),
            "reasoning_length": len(reasoning),
            "sentence_count": sentence_count,
        }

        # Log as structured data for later analysis
        logger.info(
            f"Reasoning: {agent_name}.{model_class}.{field}",
            extra={"reasoning_data": entry},
        )

        self.entry_count += 1

    async def log_corpus_batch(
        self,
        job_id: str,
        agent_name: str,
        corpus: list[dict[str, Any]],
    ) -> None:
        """Log a batch of reasoning entries from extract_reasoning_corpus().

        Args:
            job_id: Job identifier
            agent_name: Name of the agent that produced this reasoning
            corpus: List of dicts from ReasonedOutputMixin.extract_reasoning_corpus()
        """
        for entry in corpus:
            await self.log_reasoning(
                job_id=job_id,
                agent_name=agent_name,
                model_class=entry["model_class"],
                field=entry["field"],
                reasoning=entry["reasoning"],
                value=entry["value"],
            )

    def get_entry_count(self) -> int:
        """Get the total number of reasoning entries logged.

        Returns:
            Count of reasoning entries logged in this session
        """
        return self.entry_count


# Global singleton instance
_corpus_service: ReasoningCorpusService | None = None


def get_reasoning_corpus_service() -> ReasoningCorpusService:
    """Get or create the global reasoning corpus service.

    Returns:
        Singleton ReasoningCorpusService instance
    """
    global _corpus_service

    if _corpus_service is None:
        _corpus_service = ReasoningCorpusService()

    return _corpus_service


__all__ = [
    "ReasoningCorpusService",
    "get_reasoning_corpus_service",
]
