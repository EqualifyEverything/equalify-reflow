"""Text correction service for concurrent page processing with retry logic."""

import asyncio
import logging

from ..agents.text_correction_agent import (
    TextCorrectionAgent,
    get_text_correction_agent,
)
from ..config import settings
from ..services.pdf_converter import PageData
from ..shared.models.processing import PageCorrectionResult, TextCorrection
from ..utils.markdown_cleanup import (
    SpellingFlag,
    _load_technical_dictionary,
    check_spelling,
    format_spelling_flags_for_prompt,
)

logger = logging.getLogger(__name__)


class PageProcessingError(Exception):
    """Error during page processing that persisted after all retries."""

    def __init__(self, page_num: int, original_error: Exception):
        self.page_num = page_num
        self.original_error = original_error
        super().__init__(
            f"Page {page_num} failed after {settings.page_retry_attempts} attempts: {original_error}"
        )


class TextCorrectionService:
    """Service for concurrent AI-powered text correction with retry logic."""

    def __init__(
        self,
        max_concurrent_pages: int = 5,
        agent: TextCorrectionAgent | None = None,
        enable_spell_check: bool = True,
    ):
        """Initialize text correction service.

        Args:
            max_concurrent_pages: Maximum number of pages to process concurrently
            agent: Optional pre-configured TextCorrectionAgent (for testing)
            enable_spell_check: Whether to run spell checking and flag potential errors
        """
        self.max_concurrent_pages = max_concurrent_pages
        self.semaphore = asyncio.Semaphore(max_concurrent_pages)
        self._agent = agent  # Store provided agent (for testing) or None
        self._agent_initialized = agent is not None
        self.enable_spell_check = enable_spell_check
        self._technical_dictionary: set[str] | None = None  # Lazy-loaded

        logger.info(
            f"Text correction service initialized "
            f"(max_concurrent_pages={max_concurrent_pages}, "
            f"lazy_init={not self._agent_initialized}, "
            f"spell_check={enable_spell_check})"
        )

    def _ensure_agent(self) -> TextCorrectionAgent:
        """Ensure agent is initialized (lazy initialization).

        Returns:
            Initialized TextCorrectionAgent instance
        """
        if not self._agent_initialized:
            logger.info("Lazy-initializing TextCorrectionAgent on first use...")
            self._agent = get_text_correction_agent()
            self._agent_initialized = True
            logger.info("TextCorrectionAgent lazy initialization complete")

        return self._agent

    def _get_technical_dictionary(self) -> set[str]:
        """Get technical dictionary (lazy-loaded, cached).

        Returns:
            Set of technical terms that should not be flagged as misspellings
        """
        if self._technical_dictionary is None:
            self._technical_dictionary = _load_technical_dictionary()
            logger.info(
                f"Loaded technical dictionary with {len(self._technical_dictionary)} terms"
            )
        return self._technical_dictionary

    def _check_spelling_for_page(
        self, markdown: str, page_num: int
    ) -> tuple[list[SpellingFlag], str]:
        """Run spell check on page markdown and format flags for prompt.

        Args:
            markdown: Page markdown text
            page_num: Page number for logging

        Returns:
            Tuple of (spelling_flags, formatted_flags_for_prompt)
        """
        if not self.enable_spell_check:
            return [], ""

        technical_dict = self._get_technical_dictionary()
        flags, words_checked, words_flagged = check_spelling(
            markdown, technical_terms=technical_dict, max_flags=20
        )

        if flags:
            logger.debug(
                f"Page {page_num}: {words_flagged} potential misspellings flagged "
                f"out of {words_checked} words"
            )

        formatted_flags = format_spelling_flags_for_prompt(flags)
        return flags, formatted_flags

    async def process_page_with_retry(
        self, page_data: PageData, max_retries: int = 3
    ) -> PageCorrectionResult:
        """Process a single page with retry logic.

        Args:
            page_data: Page data with markdown and image
            max_retries: Maximum number of retry attempts

        Returns:
            PageCorrectionResult from successful processing

        Raises:
            PageProcessingError: If all retry attempts fail
        """
        # Ensure agent is initialized (lazy init on first use)
        agent = self._ensure_agent()

        # Run spell check on page markdown (done once, before retries)
        _, spelling_flags_prompt = self._check_spelling_for_page(
            page_data.markdown, page_data.page_num
        )

        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                result = await agent.process_page(
                    page_num=page_data.page_num,
                    page_markdown=page_data.markdown,
                    page_image_base64=page_data.image_base64,
                    retry_attempt=attempt,
                    spelling_flags=spelling_flags_prompt,
                )
                # Ensure page_number is set correctly
                result.page_number = page_data.page_num
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
    ) -> list[PageCorrectionResult]:
        """Process multiple pages concurrently with semaphore-based rate limiting.

        Args:
            pages: List of page data to process

        Returns:
            List of PageCorrectionResult in same order as input pages

        Raises:
            PageProcessingError: If any page fails after all retries
        """
        logger.info(
            f"Starting concurrent text correction analysis of {len(pages)} pages "
            f"(max {self.max_concurrent_pages} at once)"
        )

        async def process_with_semaphore(page_data: PageData) -> PageCorrectionResult:
            """Process page with semaphore to limit concurrency."""
            async with self.semaphore:
                logger.debug(
                    f"Analyzing page {page_data.page_num} for corrections "
                    f"(semaphore: {self.semaphore._value} slots available)"
                )
                result = await self.process_page_with_retry(
                    page_data, max_retries=settings.page_retry_attempts
                )

                # Log per-page correction analysis
                correction_types = [c.correction_type for c in result.corrections]
                logger.info(
                    f"Page {page_data.page_num} correction analysis: "
                    f"Corrections found: {len(result.corrections)}, "
                    f"Types: {correction_types if correction_types else 'none'}, "
                    f"Confidence: {result.overall_confidence:.2f}"
                )

                # Small delay between pages to avoid rate limits
                await asyncio.sleep(2)
                return result

        # Launch all tasks concurrently (semaphore limits active processing)
        tasks = [process_with_semaphore(page) for page in pages]

        # Gather results (will raise PageProcessingError if any page fails)
        results = await asyncio.gather(*tasks)

        logger.info(
            f"Text correction analysis complete: {len(results)} pages analyzed, "
            f"{sum(len(r.corrections) for r in results)} total corrections identified"
        )

        return results

    def apply_corrections_to_markdown(
        self, markdown: str, corrections: list[TextCorrection]
    ) -> str:
        """Apply a list of corrections to markdown text.

        Uses fuzzy matching with location context to find and replace
        original text with corrected text.

        Args:
            markdown: Original markdown text
            corrections: List of corrections to apply

        Returns:
            Corrected markdown text
        """
        if not corrections:
            logger.info("No corrections to apply")
            return markdown

        corrected = markdown
        applied_count = 0
        skipped_count = 0

        for idx, correction in enumerate(corrections, start=1):
            original_text = correction.original_text.strip()
            corrected_text = correction.corrected_text.strip()

            # Try exact match first
            if original_text in corrected:
                corrected = corrected.replace(original_text, corrected_text, 1)
                applied_count += 1

                # Detailed logging with truncation for long text
                orig_len = len(original_text)
                corr_len = len(corrected_text)
                orig_preview = original_text[:100] + ("..." if orig_len > 100 else "")
                corr_preview = corrected_text[:100] + ("..." if corr_len > 100 else "")

                logger.info(
                    f"Applied correction #{applied_count} "
                    f"(type: {correction.correction_type}, "
                    f"confidence: {correction.confidence:.2f}, "
                    f"page: {getattr(correction, 'page_number', 'unknown')}):\n"
                    f"  Original ({orig_len} chars): \"{orig_preview}\"\n"
                    f"  Corrected ({corr_len} chars): \"{corr_preview}\"\n"
                    f"  Explanation: {correction.explanation}"
                )
            else:
                # Try to find using location context
                # Look for the original text within a window around the context
                context = correction.location_context

                # Try to find the context in the markdown
                if context in corrected:
                    # Found context, now try to replace within it
                    # This is a simple approach - could be made more sophisticated
                    context_start = corrected.find(context)
                    context_end = context_start + len(context)
                    context_window = corrected[context_start:context_end]

                    if original_text in context_window:
                        # Replace within the context
                        new_context = context_window.replace(original_text, corrected_text, 1)
                        corrected = corrected[:context_start] + new_context + corrected[context_end:]
                        applied_count += 1

                        # Detailed logging for context-based replacement
                        orig_len = len(original_text)
                        corr_len = len(corrected_text)
                        orig_preview = original_text[:100] + ("..." if orig_len > 100 else "")
                        corr_preview = corrected_text[:100] + ("..." if corr_len > 100 else "")

                        logger.info(
                            f"Applied correction #{applied_count} "
                            f"(type: {correction.correction_type}, "
                            f"confidence: {correction.confidence:.2f}, "
                            f"page: {getattr(correction, 'page_number', 'unknown')}, "
                            f"method: context-based):\n"
                            f"  Original ({orig_len} chars): \"{orig_preview}\"\n"
                            f"  Corrected ({corr_len} chars): \"{corr_preview}\"\n"
                            f"  Explanation: {correction.explanation}"
                        )
                    else:
                        # Original text not found even in context
                        skipped_count += 1
                        logger.warning(
                            f"Skipped correction #{idx} "
                            f"(type: {correction.correction_type}, "
                            f"confidence: {correction.confidence:.2f}, "
                            f"page: {getattr(correction, 'page_number', 'unknown')}): "
                            f"Original text not found in location context. "
                            f"Text: '{original_text[:100]}{'...' if len(original_text) > 100 else ''}'"
                        )
                else:
                    # Context not found at all
                    # Try fuzzy matching - normalize whitespace and try again
                    normalized_original = ' '.join(original_text.split())
                    normalized_corrected_md = ' '.join(corrected.split())

                    if normalized_original in normalized_corrected_md:
                        # Found with normalized whitespace
                        # This is tricky - for now, log and skip
                        skipped_count += 1
                        logger.warning(
                            f"Skipped correction #{idx} "
                            f"(type: {correction.correction_type}, "
                            f"confidence: {correction.confidence:.2f}, "
                            f"page: {getattr(correction, 'page_number', 'unknown')}): "
                            f"Found with normalized whitespace but exact match failed. "
                            f"Manual review may be needed. "
                            f"Text: '{original_text[:100]}{'...' if len(original_text) > 100 else ''}'"
                        )
                    else:
                        skipped_count += 1
                        logger.warning(
                            f"Skipped correction #{idx} "
                            f"(type: {correction.correction_type}, "
                            f"confidence: {correction.confidence:.2f}, "
                            f"page: {getattr(correction, 'page_number', 'unknown')}): "
                            f"Location context not found in markdown. "
                            f"Text: '{original_text[:100]}{'...' if len(original_text) > 100 else ''}'"
                        )

        logger.info(
            f"Applied {applied_count}/{len(corrections)} corrections "
            f"({skipped_count} skipped)"
        )

        return corrected

    def apply_all_page_corrections(
        self, pages: list[PageData], correction_results: list[PageCorrectionResult]
    ) -> str:
        """Apply corrections from all pages and combine into final document.

        Args:
            pages: Original page data with markdown
            correction_results: Correction results for each page

        Returns:
            Combined markdown with all corrections applied
        """
        logger.info(
            f"Applying corrections to {len(pages)} pages and combining into final document"
        )

        corrected_pages: list[str] = []

        for idx, (page_data, correction_result) in enumerate(zip(pages, correction_results)):
            # Apply corrections to this page's markdown
            corrected_markdown = self.apply_corrections_to_markdown(
                page_data.markdown, correction_result.corrections
            )

            # Add page separator comment (except for first page)
            if idx > 0:
                corrected_pages.append(f"\n\n<!-- Page {page_data.page_num} -->\n\n")

            corrected_pages.append(corrected_markdown)

        combined = "".join(corrected_pages)

        # Generate detailed correction summary
        total_corrections = sum(len(r.corrections) for r in correction_results)

        # Count corrections by type
        by_type = {}
        for result in correction_results:
            for corr in result.corrections:
                by_type[corr.correction_type] = by_type.get(corr.correction_type, 0) + 1

        # Count corrections by page
        by_page = {i+1: len(r.corrections) for i, r in enumerate(correction_results) if len(r.corrections) > 0}

        # Build summary string
        type_summary = ', '.join(f'{k} ({v})' for k, v in sorted(by_type.items(), key=lambda x: x[1], reverse=True))
        page_summary = ', '.join(f'Page {k}: {v}' for k, v in sorted(by_page.items()))

        logger.info(
            f"Text Correction Summary:\n"
            f"  - Total corrections: {total_corrections}\n"
            f"  - By type: {type_summary if type_summary else 'none'}\n"
            f"  - By page: {page_summary if page_summary else 'none'}\n"
            f"  - Final markdown: {len(combined)} characters"
        )

        return combined
