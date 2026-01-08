"""Subagent tools for domain agents.

Each subagent is a specialized LLM that returns structured recommendations
with confidence scores. Parent agents review recommendations and decide
whether to apply edits via propose_edit().
"""

from .citations import CITATION_SYSTEM_PROMPT, invoke_citation_subagent
from .footnotes import FOOTNOTE_SYSTEM_PROMPT, invoke_footnote_subagent
from .lists import LIST_SEMANTICS_SYSTEM_PROMPT, invoke_list_subagent
from .page_artifacts import PAGE_ARTIFACT_SYSTEM_PROMPT, invoke_page_artifact_subagent
from .paragraph_merge import (
    PARAGRAPH_MERGE_SYSTEM_PROMPT,
    invoke_paragraph_merge_subagent,
)
from .types import SubagentResult
from .typography import TYPOGRAPHY_SYSTEM_PROMPT, invoke_typography_subagent

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
    # Base type
    "SubagentResult",
    # Subagent invoke functions
    "invoke_page_artifact_subagent",
    "invoke_footnote_subagent",
    "invoke_citation_subagent",
    "invoke_list_subagent",
    "invoke_typography_subagent",
    "invoke_paragraph_merge_subagent",
    # System prompts (for testing/inspection)
    "PAGE_ARTIFACT_SYSTEM_PROMPT",
    "FOOTNOTE_SYSTEM_PROMPT",
    "CITATION_SYSTEM_PROMPT",
    "LIST_SEMANTICS_SYSTEM_PROMPT",
    "TYPOGRAPHY_SYSTEM_PROMPT",
    "PARAGRAPH_MERGE_SYSTEM_PROMPT",
]
