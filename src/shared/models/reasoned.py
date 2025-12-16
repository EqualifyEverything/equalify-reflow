"""Reasoned[T] generic wrapper for glass-box LLM reasoning.

This module provides a generic wrapper that forces chain-of-thought reasoning
BEFORE the model makes a determination. By placing the 'reasoning' field before
'value' in the Pydantic model, the JSON schema ensures the LLM produces
its reasoning before committing to an answer.

This enables:
1. Glass-box visibility into model reasoning decisions
2. Corpus collection for analyzing reasoning patterns
3. Better calibrated confidence through explicit evidence

Example:
    >>> class ImageAnalysis(BaseModel, ReasonedOutputMixin):
    ...     image_type: Reasoned[Literal["decorative", "informative"]]
    ...     confidence: Reasoned[float]
    ...
    >>> # LLM must produce reasoning before each value
    >>> output = ImageAnalysis(
    ...     image_type=Reasoned(
    ...         reasoning="Large chart with axis labels and data points visible. Contains quantitative information.",
    ...         value="informative"
    ...     ),
    ...     confidence=Reasoned(
    ...         reasoning="Clear image, unambiguous chart structure. High confidence.",
    ...         value=0.95
    ...     )
    ... )
    >>> corpus = output.extract_reasoning_corpus()
"""

from __future__ import annotations

import logging
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Reasoned(BaseModel, Generic[T]):
    """A value with explicit reasoning that preceded its determination.

    Field ordering ensures 'reasoning' appears before 'value' in the
    JSON schema, forcing LLMs to produce chain-of-thought first.

    The reasoning should be 1-2 sentences (~10-200 chars) that:
    - State the key evidence observed
    - Connect evidence to the conclusion

    Attributes:
        reasoning: 1-2 sentence explanation of how the value was determined
        value: The actual determined value
    """

    reasoning: str = Field(
        ...,
        min_length=10,
        max_length=500,  # Allow some flexibility, but encourage brevity
        description=(
            "1-2 sentences explaining how you determined this value. "
            "State key evidence observed, then connect to your conclusion. "
            "Keep it concise - sacrifice grammar for conciseness."
        ),
    )
    value: T = Field(
        ...,
        description="The determined value based on the reasoning above.",
    )

    @field_validator("reasoning")
    @classmethod
    def validate_reasoning_quality(cls, v: str) -> str:
        """Validate reasoning quality and log verbose reasoning.

        Args:
            v: The reasoning string

        Returns:
            The validated reasoning string
        """
        # Count approximate sentences
        sentence_count = v.count(". ") + v.count("! ") + v.count("? ") + 1

        if sentence_count > 3:
            logger.debug(f"Verbose reasoning detected ({sentence_count} sentences): {v[:100]}...")

        return v

    def __repr__(self) -> str:
        """Return string representation."""
        return f"Reasoned(reasoning={self.reasoning!r}, value={self.value!r})"


class ReasonedOutputMixin:
    """Mixin providing reasoning extraction utilities for Pydantic models.

    Add this mixin to any Pydantic model that contains Reasoned[T] fields
    to enable extraction of reasoning corpus for analysis and logging.

    Example:
        >>> class AnalysisOutput(BaseModel, ReasonedOutputMixin):
        ...     layout_type: Reasoned[Literal["single", "multi"]]
        ...     complexity: Reasoned[float]
        ...
        >>> output = AnalysisOutput(...)
        >>> corpus = output.extract_reasoning_corpus()
        >>> # Returns list of dicts with field, reasoning, value, model_class
    """

    def extract_reasoning_corpus(self) -> list[dict[str, Any]]:
        """Extract all Reasoned fields for corpus storage.

        Walks through all fields in the model and extracts reasoning
        from any Reasoned[T] instances, including nested lists.

        Returns:
            List of dictionaries containing:
            - field: The field name (or field[index] for list items)
            - reasoning: The reasoning string
            - value: The determined value
            - model_class: The name of the containing model class
        """
        corpus: list[dict[str, Any]] = []

        # Access model_fields from the Pydantic model
        if not hasattr(self, "model_fields"):
            return corpus

        for field_name in self.model_fields.keys():  # type: ignore[attr-defined]
            value = getattr(self, field_name)

            if isinstance(value, Reasoned):
                corpus.append(
                    {
                        "field": field_name,
                        "reasoning": value.reasoning,
                        "value": value.value,
                        "model_class": self.__class__.__name__,
                    }
                )
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, Reasoned):
                        corpus.append(
                            {
                                "field": f"{field_name}[{i}]",
                                "reasoning": item.reasoning,
                                "value": item.value,
                                "model_class": self.__class__.__name__,
                            }
                        )
                    # Also check if list items are models with ReasonedOutputMixin
                    elif hasattr(item, "extract_reasoning_corpus"):
                        nested_corpus = item.extract_reasoning_corpus()
                        for entry in nested_corpus:
                            entry["field"] = f"{field_name}[{i}].{entry['field']}"
                        corpus.extend(nested_corpus)

            # Handle nested models with ReasonedOutputMixin
            elif hasattr(value, "extract_reasoning_corpus"):
                nested_corpus = value.extract_reasoning_corpus()
                for entry in nested_corpus:
                    entry["field"] = f"{field_name}.{entry['field']}"
                corpus.extend(nested_corpus)

        return corpus


__all__ = [
    "Reasoned",
    "ReasonedOutputMixin",
]
