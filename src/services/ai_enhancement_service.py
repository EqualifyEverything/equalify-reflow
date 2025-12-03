"""AI enhancement service for concurrent page processing with retry logic."""

import asyncio
import logging

from ..agents.accessibility_agent import (
    AccessibilityAgent,
    PageImprovementResult,
    get_accessibility_agent,
)
from ..config import settings
from ..services.pdf_converter import PageData

logger = logging.getLogger(__name__)


class PageProcessingError(Exception):
    """Error during page processing that persisted after all retries."""

    def __init__(self, page_num: int, original_error: Exception):
        self.page_num = page_num
        self.original_error = original_error
        super().__init__(
            f"Page {page_num} failed after {settings.page_retry_attempts} attempts: {original_error}"
        )


class AIEnhancementService:
    """Service for concurrent AI-powered page enhancement with retry logic."""

    def __init__(
        self, max_concurrent_pages: int = 5, agent: AccessibilityAgent | None = None
    ):
        """Initialize AI enhancement service.

        Args:
            max_concurrent_pages: Maximum number of pages to process concurrently
            agent: Optional pre-configured AccessibilityAgent (for testing)
        """
        self.max_concurrent_pages = max_concurrent_pages
        self.semaphore = asyncio.Semaphore(max_concurrent_pages)
        self.agent = agent or get_accessibility_agent()

        logger.info(
            f"AI enhancement service initialized "
            f"(max_concurrent_pages={max_concurrent_pages})"
        )

    async def process_page_with_retry(
        self, page_data: PageData, max_retries: int = 3
    ) -> PageImprovementResult:
        """Process a single page with retry logic.

        Args:
            page_data: Page data with markdown and image
            max_retries: Maximum number of retry attempts

        Returns:
            PageImprovementResult from successful processing

        Raises:
            PageProcessingError: If all retry attempts fail
        """
        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                result = await self.agent.process_page(
                    page_num=page_data.page_num,
                    page_markdown=page_data.markdown,
                    page_image_base64=page_data.image_base64,
                    retry_attempt=attempt,
                )
                return result

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Page {page_data.page_num} attempt {attempt}/{max_retries} failed: {e}"
                )

                if attempt < max_retries:
                    # Exponential backoff: 2^attempt seconds
                    wait_time = 2**attempt
                    logger.info(
                        f"Retrying page {page_data.page_num} in {wait_time}s..."
                    )
                    await asyncio.sleep(wait_time)
                else:
                    # All retries exhausted
                    raise PageProcessingError(
                        page_num=page_data.page_num, original_error=e
                    ) from e

        # Should never reach here, but for type checker
        assert last_error is not None
        raise PageProcessingError(
            page_num=page_data.page_num, original_error=last_error
        )

    async def process_pages_concurrently(
        self, pages: list[PageData]
    ) -> list[PageImprovementResult]:
        """Process multiple pages concurrently with semaphore-based rate limiting.

        Args:
            pages: List of page data to process

        Returns:
            List of PageImprovementResult in same order as input pages

        Raises:
            PageProcessingError: If any page fails after all retries
        """
        logger.info(
            f"Starting concurrent processing of {len(pages)} pages "
            f"(max {self.max_concurrent_pages} at once)"
        )

        async def process_with_semaphore(page_data: PageData) -> PageImprovementResult:
            """Process page with semaphore to limit concurrency."""
            async with self.semaphore:
                logger.debug(
                    f"Processing page {page_data.page_num} "
                    f"(semaphore: {self.semaphore._value} slots available)"
                )
                result = await self.process_page_with_retry(
                    page_data, max_retries=settings.page_retry_attempts
                )
                # Small delay between pages to avoid rate limits
                await asyncio.sleep(2)
                return result

        # Launch all tasks concurrently (semaphore limits active processing)
        tasks = [process_with_semaphore(page) for page in pages]

        # Gather results (will raise PageProcessingError if any page fails)
        results = await asyncio.gather(*tasks)

        logger.info(f"Concurrent processing complete: {len(results)} pages enhanced")

        return results

    def combine_page_markdown(
        self, results: list[PageImprovementResult], original_pages: list[PageData]
    ) -> str:
        """Combine improved markdown from all pages into single document.

        Args:
            results: List of page improvement results
            original_pages: Original page data (for page numbers)

        Returns:
            Combined markdown document
        """
        logger.info(f"Combining {len(results)} pages into final markdown")

        markdown_parts: list[str] = []

        for idx, (result, page_data) in enumerate(zip(results, original_pages)):
            # Add page separator comment (except for first page)
            if idx > 0:
                markdown_parts.append(f"\n\n<!-- Page {page_data.page_num} -->\n\n")

            markdown_parts.append(result.improved_markdown)

        combined = "".join(markdown_parts)

        logger.info(f"Combined markdown: {len(combined)} characters")

        return combined
