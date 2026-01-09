"""Tests for LLM circuit breaker integration in subagents.

Tests verify:
1. Circuit breaker is created with correct parameters
2. Circuit opens after consecutive failures
3. Circuit fails fast when open (no API call made)
4. Subagents return zero confidence when circuit is open
5. Rate limit errors (429) don't count as failures
6. Circuit breaker state is shared across all subagents
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from src.agents.subagents import (
    invoke_citation_subagent,
    invoke_footnote_subagent,
    invoke_list_subagent,
    invoke_page_artifact_subagent,
    invoke_paragraph_merge_subagent,
    invoke_typography_subagent,
    invoke_typography_subagent_batch,
    is_rate_limit_error,
    llm_circuit_breaker,
    reset_llm_circuit_breaker,
)
from src.agents.subagents.types import (
    CitationResult,
    FootnoteResult,
    ListResult,
    PageArtifactResult,
    ParagraphMergeResult,
    TypographyResult,
)
from src.utils.circuit_breaker import CircuitBreakerOpenError, CircuitState

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset circuit breaker before and after each test."""
    reset_llm_circuit_breaker()
    yield
    reset_llm_circuit_breaker()


@pytest.fixture
def mock_image() -> Image.Image:
    """Create a mock PIL Image for testing."""
    return Image.new("RGB", (100, 100), color="white")


@pytest.fixture
def mock_agent_result():
    """Create a mock agent run result."""

    def _create_result(output):
        mock_result = MagicMock()
        mock_result.output = output
        return mock_result

    return _create_result


class TestCircuitBreakerConfiguration:
    """Verify circuit breaker is configured correctly."""

    def test_circuit_breaker_created_with_correct_params(self):
        """LLM circuit breaker has expected configuration."""
        assert llm_circuit_breaker.name == "bedrock-llm"
        assert llm_circuit_breaker.config.failure_threshold == 3
        assert llm_circuit_breaker.config.success_threshold == 2
        assert llm_circuit_breaker.config.timeout == 120.0

    def test_circuit_breaker_starts_closed(self):
        """Circuit breaker starts in CLOSED state."""
        assert llm_circuit_breaker.state == CircuitState.CLOSED
        assert llm_circuit_breaker.is_closed

    def test_reset_function_works(self):
        """Reset function returns circuit to closed state."""
        # Force circuit open
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        assert llm_circuit_breaker.is_open

        # Reset
        reset_llm_circuit_breaker()

        assert llm_circuit_breaker.is_closed


class TestCircuitBreakerOpensAfterFailures:
    """Verify circuit opens after consecutive failures."""

    def test_circuit_opens_after_three_failures(self):
        """Circuit opens after failure_threshold (3) consecutive failures."""
        assert llm_circuit_breaker.is_closed

        # Record 3 failures (threshold)
        llm_circuit_breaker.record_failure()
        assert llm_circuit_breaker.is_closed

        llm_circuit_breaker.record_failure()
        assert llm_circuit_breaker.is_closed

        llm_circuit_breaker.record_failure()
        assert llm_circuit_breaker.is_open

    def test_success_resets_failure_count(self):
        """A success resets the failure counter."""
        # Record 2 failures
        llm_circuit_breaker.record_failure()
        llm_circuit_breaker.record_failure()
        assert llm_circuit_breaker.is_closed

        # Success resets count
        llm_circuit_breaker.record_success()
        assert llm_circuit_breaker.is_closed

        # Need 3 more failures to open
        llm_circuit_breaker.record_failure()
        llm_circuit_breaker.record_failure()
        assert llm_circuit_breaker.is_closed

        llm_circuit_breaker.record_failure()
        assert llm_circuit_breaker.is_open


class TestCircuitBreakerFailsFast:
    """Verify circuit fails fast when open."""

    def test_check_state_raises_when_open(self):
        """check_state raises CircuitBreakerOpenError when open."""
        # Open the circuit
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        with pytest.raises(CircuitBreakerOpenError):
            llm_circuit_breaker.check_state()


class TestRateLimitErrorHandling:
    """Verify rate limit errors don't count as failures."""

    def test_throttling_not_counted_as_failure(self):
        """Throttling errors are identified correctly."""
        error = Exception("ThrottlingException: Rate exceeded")
        assert is_rate_limit_error(error) is True

    def test_429_not_counted_as_failure(self):
        """429 status code errors are identified correctly."""
        error = Exception("HTTP 429: Too Many Requests")
        assert is_rate_limit_error(error) is True

    def test_rate_limit_text_variations(self):
        """Various rate limit error messages are identified."""
        errors = [
            Exception("rate limit exceeded"),
            Exception("ratelimit error"),
            Exception("too many requests"),
            Exception("request limit exceeded"),
            Exception("You are being throttled"),
        ]

        for error in errors:
            assert is_rate_limit_error(error) is True, f"Failed for: {error}"

    def test_regular_errors_count_as_failure(self):
        """Non-rate-limit errors are counted as failures."""
        errors = [
            Exception("Connection timeout"),
            Exception("Model not found"),
            Exception("Invalid request"),
            RuntimeError("Internal error"),
        ]

        for error in errors:
            assert is_rate_limit_error(error) is False, f"False positive for: {error}"


class TestSubagentCircuitBreakerIntegration:
    """Verify subagents integrate with circuit breaker correctly."""

    @pytest.mark.asyncio
    async def test_typography_returns_zero_confidence_when_circuit_open(
        self, mock_image
    ):
        """Typography subagent returns zero confidence when circuit is open."""
        # Open the circuit
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        result = await invoke_typography_subagent("test text", mock_image)

        assert isinstance(result, TypographyResult)
        assert result.confidence == 0.0
        assert "circuit breaker" in result.reasoning.lower()
        assert result.corrected_markdown == "test text"

    @pytest.mark.asyncio
    async def test_typography_batch_returns_zero_confidence_when_circuit_open(
        self, mock_image
    ):
        """Typography batch subagent returns zero confidence for all regions when circuit is open."""
        # Open the circuit
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        text_regions = [("target1", "text1"), ("target2", "text2")]
        results = await invoke_typography_subagent_batch(text_regions, mock_image)

        assert len(results) == 2
        for task_target, result in results:
            assert isinstance(result, TypographyResult)
            assert result.confidence == 0.0
            assert "circuit breaker" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_footnote_returns_zero_confidence_when_circuit_open(self, mock_image):
        """Footnote subagent returns zero confidence when circuit is open."""
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        result = await invoke_footnote_subagent("test markdown", mock_image)

        assert isinstance(result, FootnoteResult)
        assert result.confidence == 0.0
        assert "circuit breaker" in result.reasoning.lower()
        assert result.corrected_markdown == "test markdown"

    @pytest.mark.asyncio
    async def test_citation_returns_zero_confidence_when_circuit_open(self, mock_image):
        """Citation subagent returns zero confidence when circuit is open."""
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        result = await invoke_citation_subagent("test markdown", mock_image)

        assert isinstance(result, CitationResult)
        assert result.confidence == 0.0
        assert "circuit breaker" in result.reasoning.lower()
        assert result.bibliography_found is False

    @pytest.mark.asyncio
    async def test_list_returns_zero_confidence_when_circuit_open(self, mock_image):
        """List subagent returns zero confidence when circuit is open."""
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        result = await invoke_list_subagent("- item", mock_image)

        assert isinstance(result, ListResult)
        assert result.confidence == 0.0
        assert "circuit breaker" in result.reasoning.lower()
        assert result.corrected_markdown == "- item"

    @pytest.mark.asyncio
    async def test_page_artifact_returns_zero_confidence_when_circuit_open(
        self, mock_image
    ):
        """Page artifact subagent returns zero confidence when circuit is open."""
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        result = await invoke_page_artifact_subagent("test---text", mock_image)

        assert isinstance(result, PageArtifactResult)
        assert result.confidence == 0.0
        assert "circuit breaker" in result.reasoning.lower()
        assert result.cleaned_text == "test---text"

    @pytest.mark.asyncio
    async def test_paragraph_merge_returns_zero_confidence_when_circuit_open(
        self, mock_image
    ):
        """Paragraph merge subagent returns zero confidence when circuit is open."""
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        result = await invoke_paragraph_merge_subagent(
            "page1 end", "page2 start", mock_image, mock_image
        )

        assert isinstance(result, ParagraphMergeResult)
        assert result.confidence == 0.0
        assert "circuit breaker" in result.reasoning.lower()
        assert result.should_merge is False


class TestSubagentRecordsSuccessAndFailure:
    """Verify subagents record success/failure with circuit breaker."""

    @pytest.mark.asyncio
    async def test_typography_records_success(self, mock_image, mock_agent_result):
        """Typography subagent records success on successful call."""
        expected_output = TypographyResult(
            confidence=0.9,
            reasoning="Test",
            corrected_markdown="test",
            formatting_added=[],
        )

        with patch(
            "src.agents.subagents.typography._get_typography_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_agent_result(expected_output))
            mock_get.return_value = mock_agent

            # Record 2 failures first
            llm_circuit_breaker.record_failure()
            llm_circuit_breaker.record_failure()

            # Successful call should reset failure count
            await invoke_typography_subagent("test", mock_image)

            # Circuit should still be closed and failure count reset
            assert llm_circuit_breaker.is_closed

    @pytest.mark.asyncio
    async def test_typography_records_failure_on_error(self, mock_image):
        """Typography subagent records failure on non-rate-limit error."""
        with patch(
            "src.agents.subagents.typography._get_typography_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Connection error"))
            mock_get.return_value = mock_agent

            # Make 3 calls that fail
            await invoke_typography_subagent("test", mock_image)
            await invoke_typography_subagent("test", mock_image)
            await invoke_typography_subagent("test", mock_image)

            # Circuit should be open after 3 failures
            assert llm_circuit_breaker.is_open

    @pytest.mark.asyncio
    async def test_typography_does_not_record_failure_on_rate_limit(self, mock_image):
        """Typography subagent doesn't record failure on rate limit error."""
        with patch(
            "src.agents.subagents.typography._get_typography_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(
                side_effect=Exception("ThrottlingException: Rate exceeded")
            )
            mock_get.return_value = mock_agent

            # Make 5 rate limit errors - should NOT open circuit
            for _ in range(5):
                await invoke_typography_subagent("test", mock_image)

            # Circuit should still be closed
            assert llm_circuit_breaker.is_closed


class TestSharedCircuitBreaker:
    """Verify circuit breaker state is shared across all subagents."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_shared_across_subagents(self, mock_image):
        """Circuit breaker state is shared - failures in one affect all."""
        # Make typography fail to open circuit
        with patch(
            "src.agents.subagents.typography._get_typography_subagent"
        ) as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Error"))
            mock_get.return_value = mock_agent

            for _ in range(3):
                await invoke_typography_subagent("test", mock_image)

        # Circuit should be open
        assert llm_circuit_breaker.is_open

        # Now all other subagents should fail fast
        footnote_result = await invoke_footnote_subagent("test", mock_image)
        assert footnote_result.confidence == 0.0

        citation_result = await invoke_citation_subagent("test", mock_image)
        assert citation_result.confidence == 0.0

        list_result = await invoke_list_subagent("- item", mock_image)
        assert list_result.confidence == 0.0


class TestPageChainCircuitBreaker:
    """Test page_chain integration with circuit breaker."""

    @pytest.mark.asyncio
    async def test_analyze_page_raises_when_circuit_open(self):
        """analyze_page raises CircuitBreakerOpenError when circuit is open."""
        from src.agents.page_chain import PageChainState, analyze_page

        # Open the circuit
        for _ in range(3):
            llm_circuit_breaker.record_failure()

        with pytest.raises(CircuitBreakerOpenError):
            await analyze_page(
                page_num=1,
                markdown="# Test",
                state=PageChainState(),
                total_pages=1,
            )

    @pytest.mark.asyncio
    async def test_analyze_page_records_success(self, mock_agent_result):
        """analyze_page records success on successful call."""
        from src.agents.page_chain import (
            PageAnalysisOutput,
            PageChainState,
            analyze_page,
        )

        mock_output = PageAnalysisOutput(
            headings=[],
            summary="Test summary",
            terms=[],
        )

        # Create a mock usage object
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50

        mock_result = MagicMock()
        mock_result.output = mock_output
        mock_result.usage = MagicMock(return_value=mock_usage)

        with patch("src.agents.page_chain._get_page_analysis_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get.return_value = mock_agent

            # Record 2 failures first
            llm_circuit_breaker.record_failure()
            llm_circuit_breaker.record_failure()

            # Successful call
            await analyze_page(
                page_num=1,
                markdown="# Test",
                state=PageChainState(),
                total_pages=1,
            )

            # Circuit should still be closed (success reset failure count)
            assert llm_circuit_breaker.is_closed

    @pytest.mark.asyncio
    async def test_analyze_page_records_failure_on_error(self):
        """analyze_page records failure on non-rate-limit error."""
        from src.agents.page_chain import PageChainState, analyze_page

        with patch("src.agents.page_chain._get_page_analysis_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(side_effect=RuntimeError("Connection error"))
            mock_get.return_value = mock_agent

            # Make 3 calls that fail
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    await analyze_page(
                        page_num=1,
                        markdown="# Test",
                        state=PageChainState(),
                        total_pages=1,
                    )

            # Circuit should be open after 3 failures
            assert llm_circuit_breaker.is_open

    @pytest.mark.asyncio
    async def test_analyze_page_does_not_record_failure_on_rate_limit(self):
        """analyze_page doesn't record failure on rate limit error."""
        from src.agents.page_chain import PageChainState, analyze_page

        with patch("src.agents.page_chain._get_page_analysis_agent") as mock_get:
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(
                side_effect=Exception("ThrottlingException: Rate exceeded")
            )
            mock_get.return_value = mock_agent

            # Make 5 rate limit errors
            for _ in range(5):
                with pytest.raises(Exception, match="Throttling"):
                    await analyze_page(
                        page_num=1,
                        markdown="# Test",
                        state=PageChainState(),
                        total_pages=1,
                    )

            # Circuit should still be closed
            assert llm_circuit_breaker.is_closed
