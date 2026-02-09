"""AI agents for document processing.

Pipeline processing is handled by PipelineViewerService.
This package provides:
- model_tiers: LLM model configuration
- prompts/: Prompt templates and procedures
- models: StoredFigure (for storage service)
- events: Event bus registry (for SSE compatibility)
"""

from .model_tiers import MODEL_TIER_MAP, ModelTier
from .models import StoredFigure

__all__ = [
    "MODEL_TIER_MAP",
    "ModelTier",
    "StoredFigure",
]
