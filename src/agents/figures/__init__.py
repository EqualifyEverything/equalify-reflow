"""Figures analysis agents for Phase 3b: Refine (Specialized Agents).

This package contains the agents for figure/image analysis:
- FiguresAgent - Main orchestrator that outputs AgentResult
- classification_agent - Image type classification (Haiku)
- generation_agent - Alt text generation (Haiku)

The FiguresAgent orchestrates classification and generation, routing
high-confidence fixes to auto_corrections and low-confidence to review_items.

Reference: PRD-023 - Figures Agent Refactor
"""

from src.agents.figures.classification_agent import (
    ImageClassification,
    ImageClassificationOutput,
    classify,
)
from src.agents.figures.figures_agent import FiguresAgent, ImagePlaceholder
from src.agents.figures.generation_agent import (
    AltTextGeneration,
    AltTextGenerationOutput,
    generate,
)

__all__ = [
    # Main agent
    "FiguresAgent",
    "ImagePlaceholder",
    # Classification
    "ImageClassification",
    "ImageClassificationOutput",
    "classify",
    # Generation
    "AltTextGeneration",
    "AltTextGenerationOutput",
    "generate",
]
