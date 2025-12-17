"""Chained figures analysis agents.

This package contains the chained agents for figure/image analysis:
1. classification_agent - Image type classification (Haiku)
2. generation_agent - Alt text generation (Haiku)
3. routing - Pure Python routing logic

The chained_figures.py orchestrator coordinates these steps.
"""

from src.agents.figures.classification_agent import (
    ImageClassification,
    ImageClassificationOutput,
    classify,
)
from src.agents.figures.generation_agent import (
    AltTextGeneration,
    AltTextGenerationOutput,
    generate,
)
from src.agents.figures.routing import route_image

__all__ = [
    # Classification
    "ImageClassification",
    "ImageClassificationOutput",
    "classify",
    # Generation
    "AltTextGeneration",
    "AltTextGenerationOutput",
    "generate",
    # Routing
    "route_image",
]
