"""Unit tests for AIEnhancementService.

Tests concurrent page processing with retry logic and exponential backoff.
Validates semaphore-based rate limiting and error handling.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.ai_enhancement_service import AIEnhancementService, PageProcessingError
from src.services.pdf_converter import PageData
from src.agents.accessibility_agent import PageImprovementResult


pytestmark = pytest.mark.unit


# ============================================================================
# Initialization Tests (4 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_ai_enhancement_service_init_default_max_concurrent():
    """Test AIEnhancementService initializes with default max_concurrent_pages."""
    service = AIEnhancementService()

    assert service.max_concurrent_pages == 5  # Default from config
    assert service.semaphore._value == 5
    assert service.agent is not None


@pytest.mark.asyncio
async def test_ai_enhancement_service_init_custom_max_concurrent():
    """Test AIEnhancementService accepts custom max_concurrent_pages."""
    service = AIEnhancementService(max_concurrent_pages=3)

    assert service.max_concurrent_pages == 3
    assert service.semaphore._value == 3


@pytest.mark.asyncio
async def test_ai_enhancement_service_init_default_agent():
    """Test AIEnhancementService creates default agent if not provided."""
    service = AIEnhancementService()

    # Should have created an agent
    assert service.agent is not None


@pytest.mark.asyncio
async def test_ai_enhancement_service_init_custom_agent(mock_accessibility_agent):
    """Test AIEnhancementService accepts custom agent for testing."""
    service = AIEnhancementService(agent=mock_accessibility_agent)

    # Should use injected agent
    assert service.agent == mock_accessibility_agent


# ============================================================================
# process_page_with_retry Tests (6 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_page_with_retry_success_first_attempt(
    sample_page_data, sample_page_improvement_result, mock_accessibility_agent
):
    """Test successful page processing on first attempt."""
    service = AIEnhancementService(agent=mock_accessibility_agent)

    result = await service.process_page_with_retry(sample_page_data, max_retries=3)

    # Should succeed on first attempt
    assert result == sample_page_improvement_result
    mock_accessibility_agent.process_page.assert_called_once_with(
        page_num=sample_page_data.page_num,
        page_markdown=sample_page_data.markdown,
        page_image_base64=sample_page_data.image_base64,
        retry_attempt=1,
    )


@pytest.mark.asyncio
async def test_process_page_with_retry_success_after_one_retry(
    sample_page_data, sample_page_improvement_result, mock_accessibility_agent
):
    """Test successful page processing after 1 retry."""
    # Fail once, then succeed
    mock_accessibility_agent.process_page.side_effect = [
        Exception("Claude API rate limit"),
        sample_page_improvement_result,
    ]

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await service.process_page_with_retry(sample_page_data, max_retries=3)

        # Should succeed after retry
        assert result == sample_page_improvement_result
        assert mock_accessibility_agent.process_page.call_count == 2

        # Should have waited with exponential backoff (2^1 = 2 seconds)
        mock_sleep.assert_called_once_with(2)


@pytest.mark.asyncio
async def test_process_page_with_retry_success_after_two_retries(
    sample_page_data, sample_page_improvement_result, mock_accessibility_agent
):
    """Test successful page processing after 2 retries."""
    # Fail twice, then succeed
    mock_accessibility_agent.process_page.side_effect = [
        Exception("API timeout"),
        Exception("API timeout"),
        sample_page_improvement_result,
    ]

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await service.process_page_with_retry(sample_page_data, max_retries=3)

        # Should succeed after 2 retries
        assert result == sample_page_improvement_result
        assert mock_accessibility_agent.process_page.call_count == 3

        # Should have exponential backoff: 2^1=2, 2^2=4
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)


@pytest.mark.asyncio
async def test_process_page_with_retry_failure_after_max_retries(
    sample_page_data, mock_accessibility_agent
):
    """Test PageProcessingError raised after all retries exhausted."""
    # Always fail
    mock_accessibility_agent.process_page.side_effect = Exception(
        "Claude API unavailable"
    )

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(PageProcessingError) as exc_info:
            await service.process_page_with_retry(sample_page_data, max_retries=3)

        # Should have correct page number and error details
        assert exc_info.value.page_num == sample_page_data.page_num
        assert "Claude API unavailable" in str(exc_info.value.original_error)

        # Should have attempted 3 times
        assert mock_accessibility_agent.process_page.call_count == 3


@pytest.mark.asyncio
async def test_process_page_with_retry_exponential_backoff_timing(
    sample_page_data, mock_accessibility_agent
):
    """Test exponential backoff timing (2^attempt seconds)."""
    # Fail all attempts
    mock_accessibility_agent.process_page.side_effect = Exception("API error")

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with pytest.raises(PageProcessingError):
            await service.process_page_with_retry(sample_page_data, max_retries=3)

        # Should have backoff delays: 2^1=2, 2^2=4 (no delay after last failure)
        assert mock_sleep.call_count == 2
        calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert calls == [2, 4]


@pytest.mark.asyncio
async def test_process_page_with_retry_tracks_attempt_number(
    sample_page_data, sample_page_improvement_result, mock_accessibility_agent
):
    """Test retry_attempt parameter increments correctly."""
    # Fail first two attempts, succeed on third
    mock_accessibility_agent.process_page.side_effect = [
        Exception("Error 1"),
        Exception("Error 2"),
        sample_page_improvement_result,
    ]

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await service.process_page_with_retry(sample_page_data, max_retries=3)

        # Verify attempt numbers passed correctly
        calls = mock_accessibility_agent.process_page.call_args_list
        assert calls[0].kwargs["retry_attempt"] == 1
        assert calls[1].kwargs["retry_attempt"] == 2
        assert calls[2].kwargs["retry_attempt"] == 3


# ============================================================================
# process_pages_concurrently Tests (5 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_process_pages_concurrently_single_page(
    sample_page_data, sample_page_improvement_result, mock_accessibility_agent
):
    """Test concurrent processing of single page."""
    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        results = await service.process_pages_concurrently([sample_page_data])

        # Should return single result
        assert len(results) == 1
        assert results[0] == sample_page_improvement_result


@pytest.mark.asyncio
async def test_process_pages_concurrently_multiple_pages_under_limit(
    sample_page_improvement_result, mock_accessibility_agent
):
    """Test concurrent processing of pages under max_concurrent limit."""
    pages = [
        PageData(page_num=1, markdown="Page 1", image_base64="img1"),
        PageData(page_num=2, markdown="Page 2", image_base64="img2"),
        PageData(page_num=3, markdown="Page 3", image_base64="img3"),
    ]

    service = AIEnhancementService(max_concurrent_pages=5, agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        results = await service.process_pages_concurrently(pages)

        # Should process all pages
        assert len(results) == 3
        assert mock_accessibility_agent.process_page.call_count == 3


@pytest.mark.asyncio
async def test_process_pages_concurrently_multiple_pages_over_limit(
    sample_page_improvement_result, mock_accessibility_agent
):
    """Test concurrent processing respects max_concurrent limit."""
    pages = [
        PageData(page_num=i, markdown=f"Page {i}", image_base64=f"img{i}")
        for i in range(1, 8)  # 7 pages
    ]

    service = AIEnhancementService(max_concurrent_pages=3, agent=mock_accessibility_agent)

    concurrent_count = 0
    max_concurrent_count = 0

    async def track_concurrent_calls(*args, **kwargs):
        nonlocal concurrent_count, max_concurrent_count
        concurrent_count += 1
        max_concurrent_count = max(max_concurrent_count, concurrent_count)
        await asyncio.sleep(0.001)  # Minimal delay to allow context switch
        concurrent_count -= 1
        return sample_page_improvement_result

    mock_accessibility_agent.process_page.side_effect = track_concurrent_calls

    with patch("asyncio.sleep", new_callable=AsyncMock):
        results = await service.process_pages_concurrently(pages)

        # Should process all pages
        assert len(results) == 7

        # Should never exceed max_concurrent_pages (semaphore limit)
        assert max_concurrent_count <= 3


@pytest.mark.asyncio
async def test_process_pages_concurrently_one_page_failure_propagates(
    mock_accessibility_agent
):
    """Test failure of one page propagates PageProcessingError."""
    pages = [
        PageData(page_num=1, markdown="Page 1", image_base64="img1"),
        PageData(page_num=2, markdown="Page 2", image_base64="img2"),
        PageData(page_num=3, markdown="Page 3", image_base64="img3"),
    ]

    # Page 2 always fails
    def process_page_side_effect(*args, **kwargs):
        page_num = kwargs.get("page_num")
        if page_num == 2:
            raise Exception("Claude API error")
        return PageImprovementResult(
            improved_markdown=f"Page {page_num}", confidence_score=0.9, processing_notes="OK"
        )

    mock_accessibility_agent.process_page.side_effect = process_page_side_effect

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(PageProcessingError) as exc_info:
            await service.process_pages_concurrently(pages)

        # Should fail on page 2
        assert exc_info.value.page_num == 2


@pytest.mark.asyncio
async def test_process_pages_concurrently_preserves_order(mock_accessibility_agent):
    """Test results are returned in same order as input pages."""
    pages = [
        PageData(page_num=i, markdown=f"Page {i}", image_base64=f"img{i}")
        for i in range(1, 6)
    ]

    # Return different results for each page (with minimal delay to ensure concurrency)
    async def process_page_with_result(*args, **kwargs):
        page_num = kwargs.get("page_num")
        # Minimal delay to allow context switch
        await asyncio.sleep(0.001)
        return PageImprovementResult(
            improved_markdown=f"Improved Page {page_num}",
            confidence_score=0.8 + (page_num * 0.01),
            processing_notes=f"Page {page_num} notes",
        )

    mock_accessibility_agent.process_page.side_effect = process_page_with_result

    service = AIEnhancementService(agent=mock_accessibility_agent)

    with patch("asyncio.sleep", new_callable=AsyncMock):
        results = await service.process_pages_concurrently(pages)

        # Results should match input order (not completion order)
        assert len(results) == 5
        for i, result in enumerate(results, start=1):
            assert f"Page {i}" in result.improved_markdown
            assert result.confidence_score == 0.8 + (i * 0.01)


# ============================================================================
# combine_page_markdown Tests (3 tests)
# ============================================================================


@pytest.mark.asyncio
async def test_combine_page_markdown_single_page():
    """Test combining single page (no separator needed)."""
    service = AIEnhancementService()

    pages = [PageData(page_num=1, markdown="Original", image_base64="img")]
    results = [
        PageImprovementResult(
            improved_markdown="# Improved Page 1\n\nContent",
            confidence_score=0.9,
            processing_notes="OK",
        )
    ]

    combined = service.combine_page_markdown(results, pages)

    # Should not have page separator for single page
    assert combined == "# Improved Page 1\n\nContent"
    assert "<!-- Page" not in combined


@pytest.mark.asyncio
async def test_combine_page_markdown_multiple_pages():
    """Test combining multiple pages with separators."""
    service = AIEnhancementService()

    pages = [
        PageData(page_num=1, markdown="Page 1", image_base64="img1"),
        PageData(page_num=2, markdown="Page 2", image_base64="img2"),
        PageData(page_num=3, markdown="Page 3", image_base64="img3"),
    ]

    results = [
        PageImprovementResult(
            improved_markdown="# Page 1\n\nContent 1",
            confidence_score=0.9,
            processing_notes="OK",
        ),
        PageImprovementResult(
            improved_markdown="# Page 2\n\nContent 2",
            confidence_score=0.85,
            processing_notes="OK",
        ),
        PageImprovementResult(
            improved_markdown="# Page 3\n\nContent 3",
            confidence_score=0.95,
            processing_notes="OK",
        ),
    ]

    combined = service.combine_page_markdown(results, pages)

    # Should have page separators (except before first page)
    assert "# Page 1\n\nContent 1" in combined
    assert "<!-- Page 2 -->" in combined
    assert "# Page 2\n\nContent 2" in combined
    assert "<!-- Page 3 -->" in combined
    assert "# Page 3\n\nContent 3" in combined

    # First page should NOT have separator
    assert combined.startswith("# Page 1")


@pytest.mark.asyncio
async def test_combine_page_markdown_preserves_order():
    """Test page order is preserved in combined markdown."""
    service = AIEnhancementService()

    pages = [
        PageData(page_num=5, markdown="Page 5", image_base64="img5"),
        PageData(page_num=1, markdown="Page 1", image_base64="img1"),
        PageData(page_num=3, markdown="Page 3", image_base64="img3"),
    ]

    results = [
        PageImprovementResult(
            improved_markdown="Content from Page 5", confidence_score=0.9, processing_notes="OK"
        ),
        PageImprovementResult(
            improved_markdown="Content from Page 1", confidence_score=0.9, processing_notes="OK"
        ),
        PageImprovementResult(
            improved_markdown="Content from Page 3", confidence_score=0.9, processing_notes="OK"
        ),
    ]

    combined = service.combine_page_markdown(results, pages)

    # Should preserve input order (5, 1, 3)
    lines = combined.split("\n")
    assert "Content from Page 5" in combined
    assert "<!-- Page 1 -->" in combined
    assert "Content from Page 1" in combined
    assert "<!-- Page 3 -->" in combined
    assert "Content from Page 3" in combined

    # Order verification: Page 5 should come before Page 1, Page 1 before Page 3
    idx_5 = combined.index("Content from Page 5")
    idx_1 = combined.index("Content from Page 1")
    idx_3 = combined.index("Content from Page 3")
    assert idx_5 < idx_1 < idx_3
