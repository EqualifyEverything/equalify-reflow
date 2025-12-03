"""Text correction agent with AWS Bedrock support.

This module provides a TextCorrectionAgent that uses AWS Bedrock (Claude)
to compare page images with extracted text and identify layout/structure
corrections needed to make the markdown match the visual appearance.
"""

import base64
import logging
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.bedrock import BedrockConverseModel

from src.agents.base_agent import AgentConfig, BaseDocumentAgent
from src.config import settings
from src.shared.models.agent_models import AgentInput
from src.shared.models.processing import PageCorrectionResult

logger = logging.getLogger(__name__)


class TextCorrectionAgent(BaseDocumentAgent[PageCorrectionResult]):
    """Claude-powered agent using AWS Bedrock for text correction.

    Inherits from BaseDocumentAgent to use shared infrastructure for:
    - Model initialization (BedrockConverseModel)
    - Prompt loading (YAML with fallback)
    - Cost calculation
    - Retry logic
    """

    def _default_prompts(self) -> dict[str, Any]:
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

    def _create_model(self) -> "BedrockConverseModel":
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
        raise ValueError(f"Unsupported AI provider: {provider}")

    def _create_agent(self) -> Agent[None, PageCorrectionResult]:
        """Override to add output validator."""
        agent = super()._create_agent()

        # Register output validator to provide helpful retry messages
        @agent.output_validator
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

        return agent

    async def process(self, input_data: AgentInput) -> PageCorrectionResult:
        """Process page input and return structured output.

        Args:
            input_data: Unified input containing page markdown and image

        Returns:
            PageCorrectionResult with corrections and confidence
        """
        # Format user prompt with markdown content
        user_message = self.prompts["user_prompt_template"].format(
            page_markdown=input_data.page_markdown
        )

        # Add spelling flags if present in document context
        if input_data.document_context and "spelling_flags" in input_data.document_context:
            spelling_flags = input_data.document_context["spelling_flags"]
            if spelling_flags:
                user_message = user_message.replace(
                    "**Extracted Markdown:**",
                    f"{spelling_flags}\n\n**Extracted Markdown:**"
                )
                logger.debug(f"Page {input_data.page_number}: Added spelling flags to prompt")

        # Decode base64 image to bytes
        try:
            image_bytes = base64.b64decode(input_data.page_image_base64)
        except Exception as e:
            logger.error(f"Failed to decode base64 image: {e}")
            raise ValueError("Invalid base64 image data") from e

        # Run agent
        output, usage = await self._run_agent(user_message, image_bytes)

        # Attach usage to output (PageCorrectionResult has a usage field)
        output.usage = usage
        output.page_number = input_data.page_number

        logger.info(
            f"Page {input_data.page_number} analyzed: {len(output.corrections)} corrections found "
            f"(confidence: {output.overall_confidence:.2f})"
        )

        return output



# Global agent instance (singleton pattern)
_agent_instance: TextCorrectionAgent | None = None


def get_text_correction_agent() -> TextCorrectionAgent:
    """Get or create the global text correction agent instance.

    Returns:
        Singleton TextCorrectionAgent instance
    """
    global _agent_instance

    if _agent_instance is None:
        config = AgentConfig(
            name="text_correction_agent",
            prompts_file=Path("text_correction.yaml"),  # Relative to agent_prompts_dir
            output_type=PageCorrectionResult,
            correction_types=[
                "heading_level",
                "list_structure",
                "table_format",
                "paragraph_break",
                "spelling",
            ],
        )
        _agent_instance = TextCorrectionAgent(config)

    return _agent_instance
