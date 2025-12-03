"""Text correction agent with AWS Bedrock support.

This module provides a TextCorrectionAgent that uses AWS Bedrock (Claude)
to compare page images with extracted text and identify layout/structure
corrections needed to make the markdown match the visual appearance.
"""

import base64
import logging
from pathlib import Path

import yaml
from pydantic import ValidationError
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import BinaryContent

from ..config import settings
from ..shared.models.processing import LLMUsage, PageCorrectionResult

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

        # Initialize PydanticAI agent with retries for output validation
        logger.info("Creating PydanticAI Agent with BedrockConverseModel...")
        try:
            self.agent: Agent[None, PageCorrectionResult] = Agent(
                model,
                output_type=PageCorrectionResult,
                system_prompt=prompts["system_prompt"],
                retries=2,  # Allow 2 retries for output validation failures
            )

            # Register output validator to provide helpful retry messages
            @self.agent.output_validator
            async def validate_corrections(
                ctx: RunContext[None], output: PageCorrectionResult
            ) -> PageCorrectionResult:
                """Validate corrections and provide helpful retry messages."""
                errors = []
                for i, correction in enumerate(output.corrections):
                    if not correction.original_text or not correction.original_text.strip():
                        errors.append(f"Correction {i+1}: original_text cannot be empty")
                    if not correction.corrected_text or not correction.corrected_text.strip():
                        errors.append(f"Correction {i+1}: corrected_text cannot be empty")
                    if not correction.explanation or not correction.explanation.strip():
                        errors.append(f"Correction {i+1}: explanation cannot be empty")

                if errors:
                    raise ModelRetry(
                        f"Invalid corrections - please fix: {'; '.join(errors)}. "
                        "Each correction must have non-empty original_text, corrected_text, and explanation."
                    )
                return output

            logger.info("PydanticAI Agent created successfully with output validator")
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

    def _calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in cents using Bedrock Claude Haiku pricing.

        Pricing (Claude 3 Haiku):
        - Input: $0.25 per 1M tokens = $0.00000025 per token = 0.000025 cents per token
        - Output: $1.25 per 1M tokens = $0.00000125 per token = 0.000125 cents per token

        Args:
            input_tokens: Number of input tokens consumed
            output_tokens: Number of output tokens generated

        Returns:
            Cost in cents (float)
        """
        input_cost_per_token_cents = 0.000025  # $0.25/1M tokens in cents
        output_cost_per_token_cents = 0.000125  # $1.25/1M tokens in cents

        return (input_tokens * input_cost_per_token_cents) + (
            output_tokens * output_cost_per_token_cents
        )

    async def process_page(
        self,
        page_num: int,
        page_markdown: str,
        page_image_base64: str,
        retry_attempt: int = 1,
        spelling_flags: str = "",
    ) -> PageCorrectionResult:
        """Process a single PDF page to identify text corrections.

        Args:
            page_num: Page number (for logging and result)
            page_markdown: Extracted markdown text from Docling
            page_image_base64: Base64-encoded PNG image of the page
            retry_attempt: Current retry attempt number (for logging)
            spelling_flags: Formatted spelling flags to include in prompt (optional)

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

        # Add spelling flags if present (before the markdown)
        if spelling_flags:
            # Insert spelling flags before the markdown section
            user_message = user_message.replace(
                "**Extracted Markdown:**",
                f"{spelling_flags}\n\n**Extracted Markdown:**"
            )
            logger.debug(f"Page {page_num}: Added spelling flags to prompt")

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

            # Capture token usage and calculate cost
            usage = result.usage()
            input_tokens = usage.input_tokens or 0
            output_tokens = usage.output_tokens or 0
            cost_cents = self._calculate_cost(input_tokens, output_tokens)

            # Add usage to the output
            output = result.output
            output.usage = LLMUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_cents=cost_cents,
            )

            logger.info(
                f"Page {page_num} analyzed: {len(output.corrections)} corrections found "
                f"(confidence: {output.overall_confidence:.2f}, "
                f"tokens: {input_tokens}/{output_tokens}, cost: ${cost_cents/100:.6f})"
            )

            return output

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
