"""Text correction agent with AWS Bedrock support.

This module provides a TextCorrectionAgent that uses AWS Bedrock (Claude)
to compare page images with extracted text and identify layout/structure
corrections needed to make the markdown match the visual appearance.
"""

import base64
import logging
from pathlib import Path

import yaml
from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from ..config import settings
from ..shared.models.processing import PageCorrectionResult

logger = logging.getLogger(__name__)


class TextCorrectionAgent:
    """Claude-powered agent using AWS Bedrock for text correction."""

    def __init__(self) -> None:
        """Initialize the text correction agent with AWS Bedrock."""
        logger.info("TextCorrectionAgent.__init__() started")

        # Load prompts from YAML config
        logger.info("Loading prompts from YAML...")
        prompts = self._load_prompts()
        logger.info("Prompts loaded successfully")

        # Create model based on configured provider
        logger.info("Creating Bedrock model...")
        model = self._create_model()
        logger.info(f"Bedrock model created: {type(model).__name__}")

        # Initialize PydanticAI agent
        logger.info("Creating PydanticAI Agent with BedrockConverseModel...")
        try:
            self.agent: Agent[None, PageCorrectionResult] = Agent(
                model,
                output_type=PageCorrectionResult,
                system_prompt=prompts["system_prompt"],
            )
            logger.info("PydanticAI Agent created successfully")
        except Exception as e:
            logger.error(f"Failed to create PydanticAI Agent: {e}", exc_info=True)
            raise

        logger.info("Setting user prompt template...")
        self.user_prompt_template = prompts["user_prompt_template"]

        logger.info(
            f"Text correction agent initialized with provider: {settings.ai_provider}"
        )

    def _create_model(self):
        """Create AWS Bedrock model for Claude access.

        Uses boto3.Session with explicit credentials to bypass the credential provider
        chain (including IMDS) and connect directly to AWS Bedrock.

        Returns:
            BedrockConverseModel instance for PydanticAI Agent

        Raises:
            ValueError: If ai_provider is not set to 'bedrock'
            KeyError: If required AWS credentials are missing from environment
        """
        provider = settings.ai_provider.lower()

        if provider == "bedrock":
            logger.info("Importing PydanticAI bedrock model...")
            from pydantic_ai.models.bedrock import BedrockConverseModel
            logger.info("Imports complete")

            # BedrockConverseModel uses boto3 credentials and region from environment
            # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN set via docker-compose
            # AWS_DEFAULT_REGION set to us-east-1 in docker-compose
            # AWS_EC2_METADATA_DISABLED=true prevents IMDS hang
            # Service-specific endpoints ensure Bedrock uses real AWS (not LocalStack)
            logger.info(
                f"Creating BedrockConverseModel with model: {settings.bedrock_model_id} "
                f"(using AWS_DEFAULT_REGION from environment: {settings.bedrock_region})"
            )

            model = BedrockConverseModel(
                model_name=settings.bedrock_model_id,
            )

            logger.info(f"BedrockConverseModel created successfully: {type(model)}")
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
        prompts_file = Path("config/text_correction_prompts.yaml")

        try:
            with open(prompts_file) as f:
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
            "system_prompt": (
                "You are a text correction expert who compares visual document "
                "layout to extracted markdown text.\n\n"
                "You will receive:\n"
                "1. A visual image of a PDF page showing the actual layout\n"
                "2. The markdown text extracted by Docling from that page\n\n"
                "Your task:\n"
                "- Compare the visual layout to the extracted markdown\n"
                "- Identify layout/structure errors: heading levels, list formatting, "
                "table structure, paragraph breaks\n"
                "- Return a list of specific corrections to match the visual layout\n"
                "- For each correction: original text, corrected text, location context, "
                "confidence, and explanation\n"
                "- Focus on structural/layout issues, NOT content or accessibility\n\n"
                "Return a structured list of corrections with overall confidence score."
            ),
            "user_prompt_template": (
                "Compare this visual page image to the extracted markdown and identify "
                "layout/structure corrections:\n\n"
                "**Extracted Markdown:**\n{page_markdown}\n\n"
                "**Instructions:**\n"
                "1. Look at the page image carefully - note heading levels (size, weight), "
                "list types (bullets vs. numbers), table structures, paragraph breaks\n"
                "2. Compare to the extracted markdown above\n"
                "3. Identify where the markdown structure doesn't match the visual layout\n"
                "4. Return specific corrections with location context\n\n"
                "Focus on: heading levels, list structures, table formatting, paragraph breaks.\n"
                "Do NOT focus on: content changes, accessibility improvements, or alt text."
            ),
        }

    async def process_page(
        self,
        page_num: int,
        page_markdown: str,
        page_image_base64: str,
        retry_attempt: int = 1,
    ) -> PageCorrectionResult:
        """Process a single PDF page to identify text corrections.

        Args:
            page_num: Page number (for logging and result)
            page_markdown: Extracted markdown text from Docling
            page_image_base64: Base64-encoded PNG image of the page
            retry_attempt: Current retry attempt number (for logging)

        Returns:
            PageCorrectionResult with list of corrections and confidence

        Raises:
            Exception: If AI processing fails after all retries
        """
        logger.info(
            f"Processing page {page_num} for text corrections with {settings.ai_provider} "
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
                f"Page {page_num} analyzed: {len(result.output.corrections)} corrections found "
                f"(confidence: {result.output.overall_confidence:.2f})"
            )

            return result.output

        except Exception as e:
            logger.error(
                f"Page {page_num} correction analysis failed (attempt {retry_attempt}): {e}",
                exc_info=True,
            )
            raise


# Global agent instance (singleton pattern)
_agent_instance: TextCorrectionAgent | None = None


def get_text_correction_agent() -> TextCorrectionAgent:
    """Get or create the global text correction agent instance.

    Returns:
        Singleton TextCorrectionAgent instance
    """
    global _agent_instance

    if _agent_instance is None:
        _agent_instance = TextCorrectionAgent()

    return _agent_instance
