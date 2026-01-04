"""Subagents for specialized tasks in the refine pipeline.

These subagents are called via tool use by the main refine agent
to handle figures, tables, and verification tasks.

Each subagent receives:
1. Cropped image of the element (detail view)
2. Full page image with red highlight box (context view)
"""

from __future__ import annotations

import base64
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.factory import load_prompts
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.config import settings

from .tools import FigureDescription, TableTranscription, VerificationResult

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

# =============================================================================
# Constants
# =============================================================================

MODEL_TIER = ModelTier.EFFICIENT  # Haiku 4.5
PROMPTS_FILE = "refine.yaml"


# =============================================================================
# Helpers
# =============================================================================


def _pil_to_base64(image: Image.Image) -> str:
    """Convert PIL Image to base64 string."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def _pil_to_bytes(image: Image.Image) -> bytes:
    """Convert PIL Image to bytes."""
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# =============================================================================
# Figure Subagent
# =============================================================================

_figure_agent: Agent[None, FigureDescription] | None = None


def _get_figure_agent() -> Agent[None, FigureDescription]:
    """Get or create the figure description subagent."""
    global _figure_agent

    if _figure_agent is None:
        prompts = load_prompts(PROMPTS_FILE)
        model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

        _figure_agent = Agent(
            model,
            output_type=FigureDescription,
            system_prompt=prompts.get("figure_subagent_system", ""),
        )
        logger.info("Figure subagent initialized")

    return _figure_agent


async def get_figure_description(
    cropped_image: Image.Image,
    highlighted_page: Image.Image,
    page_num: int,
    caption: str = "",
) -> FigureDescription:
    """Get alt-text description for a figure.

    Args:
        cropped_image: Cropped image of just the figure
        highlighted_page: Full page with red box around figure
        page_num: Page number for context
        caption: Caption text if present

    Returns:
        FigureDescription with alt-text and metadata
    """
    agent = _get_figure_agent()
    prompts = load_prompts(PROMPTS_FILE)

    # Build user prompt
    user_prompt = prompts.get("figure_subagent_user", "").format(
        page=page_num,
        caption=caption if caption else "No caption available",
    )

    # Prepare images as binary content
    cropped_bytes = _pil_to_bytes(cropped_image)
    highlighted_bytes = _pil_to_bytes(highlighted_page)

    try:
        result = await agent.run(
            [
                "Here is the cropped figure:",
                BinaryContent(data=cropped_bytes, media_type="image/png"),
                "Here is the full page with the figure highlighted in red:",
                BinaryContent(data=highlighted_bytes, media_type="image/png"),
                user_prompt,
            ]
        )

        logger.info(
            f"Figure description generated for page {page_num}: "
            f"type={result.output.figure_type}, confidence={result.output.confidence}"
        )

        return result.output

    except Exception as e:
        logger.error(f"Figure subagent failed for page {page_num}: {e}")
        return FigureDescription(
            alt_text=f"[Figure description failed: {str(e)}]",
            figure_type="unknown",
            confidence=0.0,
        )


# =============================================================================
# Table Subagent
# =============================================================================

_table_agent: Agent[None, TableTranscription] | None = None


def _get_table_agent() -> Agent[None, TableTranscription]:
    """Get or create the table transcription subagent."""
    global _table_agent

    if _table_agent is None:
        prompts = load_prompts(PROMPTS_FILE)
        model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

        _table_agent = Agent(
            model,
            output_type=TableTranscription,
            system_prompt=prompts.get("table_subagent_system", ""),
        )
        logger.info("Table subagent initialized")

    return _table_agent


async def get_table_transcription(
    cropped_image: Image.Image,
    highlighted_page: Image.Image,
    page_num: int,
    caption: str = "",
) -> TableTranscription:
    """Get markdown table transcription.

    Args:
        cropped_image: Cropped image of just the table
        highlighted_page: Full page with red box around table
        page_num: Page number for context
        caption: Caption text if present

    Returns:
        TableTranscription with markdown and metadata
    """
    agent = _get_table_agent()
    prompts = load_prompts(PROMPTS_FILE)

    # Build user prompt
    user_prompt = prompts.get("table_subagent_user", "").format(
        page=page_num,
        caption=caption if caption else "No caption available",
    )

    # Prepare images as binary content
    cropped_bytes = _pil_to_bytes(cropped_image)
    highlighted_bytes = _pil_to_bytes(highlighted_page)

    try:
        result = await agent.run(
            [
                "Here is the cropped table:",
                BinaryContent(data=cropped_bytes, media_type="image/png"),
                "Here is the full page with the table highlighted in red:",
                BinaryContent(data=highlighted_bytes, media_type="image/png"),
                user_prompt,
            ]
        )

        logger.info(
            f"Table transcription generated for page {page_num}: "
            f"{result.output.row_count}x{result.output.col_count}, "
            f"confidence={result.output.confidence}"
        )

        return result.output

    except Exception as e:
        logger.error(f"Table subagent failed for page {page_num}: {e}")
        return TableTranscription(
            markdown_table=f"| Error | {str(e)} |\n|-------|---------|",
            row_count=1,
            col_count=2,
            has_header=False,
            confidence=0.0,
        )


# =============================================================================
# Verification Subagent
# =============================================================================

_verify_agent: Agent[None, VerificationResult] | None = None


def _get_verify_agent() -> Agent[None, VerificationResult]:
    """Get or create the verification subagent."""
    global _verify_agent

    if _verify_agent is None:
        prompts = load_prompts(PROMPTS_FILE)
        model = BedrockConverseModel(MODEL_TIER_MAP[MODEL_TIER])

        _verify_agent = Agent(
            model,
            output_type=VerificationResult,
            system_prompt=prompts.get("verification_subagent_system", ""),
        )
        logger.info("Verification subagent initialized")

    return _verify_agent


async def verify_against_image(
    section_markdown: str,
    page_image: Image.Image,
    page_num: int,
    cropped_image: Image.Image | None = None,
    highlighted_page: Image.Image | None = None,
) -> VerificationResult:
    """Verify markdown section matches the source image.

    Args:
        section_markdown: Markdown text to verify
        page_image: Full page image (used if no cropped/highlighted provided)
        page_num: Page number for context
        cropped_image: Optional cropped region to verify
        highlighted_page: Optional page with highlight box

    Returns:
        VerificationResult with match status and issues
    """
    agent = _get_verify_agent()
    prompts = load_prompts(PROMPTS_FILE)

    # Build user prompt
    user_prompt = prompts.get("verification_subagent_user", "").format(
        page=page_num,
        section_markdown=section_markdown,
    )

    # Prepare images
    messages: list[Any] = []

    if cropped_image is not None and highlighted_page is not None:
        # Use both cropped and highlighted views
        messages.extend([
            "Here is the cropped section to verify:",
            BinaryContent(data=_pil_to_bytes(cropped_image), media_type="image/png"),
            "Here is the full page with the section highlighted:",
            BinaryContent(data=_pil_to_bytes(highlighted_page), media_type="image/png"),
        ])
    else:
        # Use full page only
        messages.extend([
            "Here is the page image to verify against:",
            BinaryContent(data=_pil_to_bytes(page_image), media_type="image/png"),
        ])

    messages.append(user_prompt)

    try:
        result = await agent.run(messages)

        logger.info(
            f"Verification for page {page_num}: matches={result.output.matches}, "
            f"issues={len(result.output.issues)}"
        )

        return result.output

    except Exception as e:
        logger.error(f"Verification subagent failed for page {page_num}: {e}")
        return VerificationResult(
            matches=False,
            issues=[f"Verification failed: {str(e)}"],
            suggested_fixes=[],
        )


# =============================================================================
# Reset for Testing
# =============================================================================


def reset_subagents() -> None:
    """Reset all subagent singletons for testing."""
    global _figure_agent, _table_agent, _verify_agent
    _figure_agent = None
    _table_agent = None
    _verify_agent = None
    logger.debug("All subagents reset")
