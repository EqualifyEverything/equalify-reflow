"""Document accessibility agent with AWS Bedrock support.

This module provides an AccessibilityAgent that uses AWS Bedrock
for Claude model access in both development and production environments.
"""

import base64
import logging
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from ..config import settings

logger = logging.getLogger(__name__)


class PageImprovementResult(BaseModel):
    """Result of AI-powered page accessibility improvement.

    Attributes:
        improved_markdown: Enhanced markdown with proper accessibility markup
        confidence_score: AI confidence in conversion quality (0.0-1.0)
        processing_notes: Brief notes about changes made or issues encountered
    """

    improved_markdown: str = Field(
        ..., description="Markdown with improved accessibility markup"
    )
    confidence_score: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence in conversion quality"
    )
    processing_notes: str = Field(
        ..., description="Notes about changes made or issues"
    )


class AccessibilityAgent:
    """Claude-powered agent using AWS Bedrock."""

    def __init__(self) -> None:
        """Initialize the accessibility agent with AWS Bedrock."""
        # Load prompts from YAML config
        prompts = self._load_prompts()

        # Create model based on configured provider
        model = self._create_model()

        # Initialize PydanticAI agent
        self.agent: Agent[None, PageImprovementResult] = Agent(
            model,
            output_type=PageImprovementResult,
            system_prompt=prompts["system_prompt"],
        )

        self.user_prompt_template = prompts["user_prompt_template"]

        logger.info(
            f"Accessibility agent initialized with provider: {settings.ai_provider}"
        )

    def _create_model(self):
        """Create AWS Bedrock model for Claude access.

        Returns:
            BedrockConverseModel instance for PydanticAI Agent

        Raises:
            ValueError: If ai_provider is not set to 'bedrock'
        """
        provider = settings.ai_provider.lower()

        if provider == "bedrock":
            from pydantic_ai.models.bedrock import BedrockConverseModel

            # BedrockConverseModel takes model_name as the model ID string
            # Region is configured via AWS SDK (boto3) environment or credentials
            model = BedrockConverseModel(
                model_name=settings.bedrock_model_id,
            )
            logger.info(
                f"Using AWS Bedrock model: {settings.bedrock_model_id}"
            )
            return model

        else:
            raise ValueError(
                f"Unsupported AI provider: {provider}. "
                f"Only 'bedrock' is supported."
            )

    def _load_prompts(self) -> dict:
        """Load prompt templates from YAML configuration.

        Returns:
            Dictionary with system_prompt and user_prompt_template
        """
        prompts_file = Path(settings.agent_prompts_dir) / "accessibility.yaml"

        try:
            with open(prompts_file, "r") as f:
                prompts = yaml.safe_load(f)
                return prompts
        except FileNotFoundError:
            logger.warning(
                f"Prompts file not found: {prompts_file}, using default prompts"
            )
            return self._default_prompts()

    def _default_prompts(self) -> dict:
        """Fallback default prompts if YAML file is not found."""
        return {
            "system_prompt": """You are an expert accessibility specialist who improves PDF-to-markdown conversions.

You will receive extracted markdown text and a visual image of the PDF page.

Your task:
1. Fix heading hierarchy to match visual structure
2. Add descriptive alt text for images
3. Ensure proper semantic markup
4. Preserve all original content

Return improved markdown with a confidence score (0-1).""",
            "user_prompt_template": """Improve this PDF page conversion:

**Extracted Markdown:**
{page_markdown}

Fix heading hierarchy, add alt text, ensure semantic markup.""",
        }

    async def process_page(
        self,
        page_num: int,
        page_markdown: str,
        page_image_base64: str,
        retry_attempt: int = 1,
    ) -> PageImprovementResult:
        """Process a single PDF page with multimodal AI analysis.

        Args:
            page_num: Page number (for logging)
            page_markdown: Extracted markdown text
            page_image_base64: Base64-encoded PNG image of the page
            retry_attempt: Current retry attempt number (for logging)

        Returns:
            PageImprovementResult with enhanced markdown and confidence

        Raises:
            Exception: If AI processing fails after all retries
        """
        logger.info(
            f"Processing page {page_num} with {settings.ai_provider} "
            f"(attempt {retry_attempt})"
        )

        # Format user prompt with markdown content
        user_message = self.user_prompt_template.format(page_markdown=page_markdown)

        # Decode base64 image to bytes for BinaryContent
        image_bytes = base64.b64decode(page_image_base64)

        try:
            # Run agent with multimodal input (text + image)
            result = await self.agent.run(
                [
                    user_message,
                    BinaryContent(data=image_bytes, media_type="image/png"),
                ],
                model_settings={
                    "max_tokens": settings.claude_max_tokens,
                    "temperature": settings.claude_temperature,
                },
            )

            logger.info(
                f"Page {page_num} processed successfully "
                f"(confidence: {result.output.confidence_score:.2f})"
            )

            return result.output

        except Exception as e:
            logger.error(
                f"Page {page_num} processing failed (attempt {retry_attempt}): {e}",
                exc_info=True,
            )
            raise


# Global agent instance (singleton pattern)
_agent_instance: Optional[AccessibilityAgent] = None


def get_accessibility_agent() -> AccessibilityAgent:
    """Get or create the global accessibility agent instance.

    Returns:
        Singleton AccessibilityAgent instance
    """
    global _agent_instance

    if _agent_instance is None:
        _agent_instance = AccessibilityAgent()

    return _agent_instance
