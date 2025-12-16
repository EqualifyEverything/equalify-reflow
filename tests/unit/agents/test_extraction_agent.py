"""Tests for ExtractionAgent.

Tests cover:
- Agent configuration and initialization
- Model tier selection (EFFICIENT = Haiku)
- Output model validation with Reasoned[T] fields
- Manifest parsing and formatting
- Heading tree formatting
- Page features formatting
- Cost calculation with Haiku pricing
- Image message building
- Reasoning corpus extraction
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from src.agents.core import AgentConfig
from src.agents.extraction_agent import (
    ExtractionAgent,
    ExtractionOutput,
)
from src.agents.model_tiers import MODEL_TIER_MAP, ModelTier
from src.services.pdf_converter import PageData
from src.shared.llm_cost import HAIKU_PRICING, calculate_estimated_cost
from src.shared.models.reasoned import Reasoned
from src.shared.models.remediation import (
    DocumentManifest,
    HeadingNode,
    HeadingTree,
    PageFeatures,
)

# =============================================================================
# Output Model Tests
# =============================================================================


def _create_extraction_output(
    markdown: str = "# Title\n\nContent here.",
    confidence_value: float = 0.9,
    confidence_reasoning: str = "Clear images, all text readable. 0.9.",
    reading_order_value: bool = True,
    reading_order_reasoning: str = "Single-column layout, top-to-bottom. True.",
    pages_transcribed: list[int] | None = None,
    transcription_notes: str = "",
) -> ExtractionOutput:
    """Helper to create ExtractionOutput with Reasoned[T] fields."""
    return ExtractionOutput(
        markdown=markdown,
        confidence=Reasoned(reasoning=confidence_reasoning, value=confidence_value),
        reading_order_followed=Reasoned(reasoning=reading_order_reasoning, value=reading_order_value),
        pages_transcribed=pages_transcribed or [1],
        transcription_notes=transcription_notes,
    )


@pytest.mark.unit
class TestExtractionOutput:
    """Tests for ExtractionOutput model with Reasoned[T] fields."""

    def test_valid_extraction_output(self) -> None:
        """Test creating a valid ExtractionOutput with Reasoned[T] fields."""
        output = _create_extraction_output(
            markdown="# Title\n\nContent here.",
            confidence_value=0.9,
            confidence_reasoning="Clear images, all text visible. 0.9.",
            reading_order_value=True,
            reading_order_reasoning="Single-column, top-to-bottom order. True.",
            pages_transcribed=[1, 2, 3],
            transcription_notes="Transcription complete.",
        )
        assert output.markdown == "# Title\n\nContent here."
        assert output.confidence.value == 0.9
        assert output.confidence.reasoning == "Clear images, all text visible. 0.9."
        assert output.reading_order_followed.value is True
        assert output.pages_transcribed == [1, 2, 3]
        assert output.transcription_notes == "Transcription complete."

    def test_extraction_output_defaults(self) -> None:
        """Test ExtractionOutput default values."""
        output = ExtractionOutput(
            markdown="# Test",
            confidence=Reasoned(reasoning="Good quality images.", value=0.85),
            reading_order_followed=Reasoned(reasoning="Simple layout.", value=True),
            pages_transcribed=[1],
        )
        assert output.transcription_notes == ""

    def test_confidence_validation(self) -> None:
        """Test confidence must be between 0 and 1."""
        with pytest.raises(ValidationError):
            ExtractionOutput(
                markdown="test",
                confidence=Reasoned(reasoning="test", value=1.5),
                reading_order_followed=Reasoned(reasoning="test", value=True),
                pages_transcribed=[1],
            )
        with pytest.raises(ValidationError):
            ExtractionOutput(
                markdown="test",
                confidence=Reasoned(reasoning="test", value=-0.1),
                reading_order_followed=Reasoned(reasoning="test", value=True),
                pages_transcribed=[1],
            )

    def test_markdown_required(self) -> None:
        """Test markdown field is required."""
        with pytest.raises(ValidationError):
            ExtractionOutput(
                confidence=Reasoned(reasoning="test", value=0.9),
                reading_order_followed=Reasoned(reasoning="test", value=True),
                pages_transcribed=[1],
            )

    def test_reasoning_required(self) -> None:
        """Test reasoning fields are required (cannot have empty reasoning)."""
        with pytest.raises(ValidationError):
            ExtractionOutput(
                markdown="test",
                confidence=Reasoned(reasoning="", value=0.9),  # Empty reasoning
                reading_order_followed=Reasoned(reasoning="test", value=True),
                pages_transcribed=[1],
            )

    def test_extract_reasoning_corpus(self) -> None:
        """Test reasoning corpus extraction from Reasoned[T] fields."""
        output = _create_extraction_output(
            confidence_value=0.92,
            confidence_reasoning="All pages clear, 2 [?] markers. 0.92.",
            reading_order_value=True,
            reading_order_reasoning="Pages 1-3 single-column. True.",
            pages_transcribed=[1, 2, 3],
        )
        corpus = output.extract_reasoning_corpus()

        # Should extract reasoning from both Reasoned[T] fields
        assert len(corpus) == 2

        # Check confidence reasoning
        confidence_entry = next(c for c in corpus if c["field"] == "confidence")
        assert confidence_entry["reasoning"] == "All pages clear, 2 [?] markers. 0.92."
        assert confidence_entry["value"] == 0.92
        assert confidence_entry["model_class"] == "ExtractionOutput"

        # Check reading_order_followed reasoning
        reading_entry = next(c for c in corpus if c["field"] == "reading_order_followed")
        assert reading_entry["reasoning"] == "Pages 1-3 single-column. True."
        assert reading_entry["value"] is True


# =============================================================================
# Agent Configuration Tests
# =============================================================================


@pytest.mark.unit
class TestExtractionAgentConfig:
    """Tests for ExtractionAgent configuration."""

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    def test_default_config(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test default configuration values when no config provided."""
        mock_yaml.return_value = {
            "system_prompt": "test prompt",
            "user_prompt": "test user prompt",
        }
        agent = ExtractionAgent()
        assert agent.config.prompts_file == Path("extraction.yaml")
        assert agent.config.max_retries == 2
        assert agent.config.temperature == 0.2
        assert agent.config.max_tokens == 16384

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    def test_custom_config(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test custom configuration values."""
        mock_yaml.return_value = {
            "system_prompt": "test prompt",
            "user_prompt": "test user prompt",
        }
        config = AgentConfig(
            name="test_extraction",
            prompts_file=Path("custom.yaml"),
            output_type=ExtractionOutput,
            max_retries=3,
            temperature=0.1,
            max_tokens=8192,
        )
        agent = ExtractionAgent(config)
        assert agent.config.prompts_file == Path("custom.yaml")
        assert agent.config.max_retries == 3
        assert agent.config.temperature == 0.1
        assert agent.config.max_tokens == 8192


# =============================================================================
# Agent Initialization Tests
# =============================================================================


@pytest.mark.unit
class TestExtractionAgentInit:
    """Tests for ExtractionAgent initialization."""

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    def test_agent_uses_efficient_tier(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test agent uses EFFICIENT (Haiku) model tier."""
        mock_yaml.return_value = {
            "system_prompt": "test prompt",
            "user_prompt": "test user prompt",
        }
        agent = ExtractionAgent()
        assert agent.model_tier == ModelTier.EFFICIENT
        assert agent.model_id == MODEL_TIER_MAP[ModelTier.EFFICIENT]
        assert "haiku" in agent.model_id.lower()

    @patch("src.agents.core.yaml.safe_load")
    @patch("builtins.open", create=True)
    def test_agent_loads_prompts(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test agent loads prompts from YAML."""
        mock_yaml.return_value = {
            "system_prompt": "custom system",
            "user_prompt": "custom user",
        }
        agent = ExtractionAgent()
        assert agent.prompts["system_prompt"] == "custom system"
        assert agent.prompts["user_prompt"] == "custom user"

    def test_agent_raises_when_prompts_file_not_found(self) -> None:
        """Test agent raises FileNotFoundError when prompts file not found (fail fast)."""
        config = AgentConfig(
            name="test_extraction",
            prompts_file=Path("nonexistent.yaml"),
            output_type=ExtractionOutput,
        )
        with pytest.raises(FileNotFoundError):
            ExtractionAgent(config)


# =============================================================================
# Heading Tree Formatting Tests
# =============================================================================


@pytest.mark.unit
class TestHeadingTreeFormatting:
    """Tests for heading tree formatting."""

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_heading_tree_basic(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test basic heading tree formatting."""
        agent = ExtractionAgent()
        tree = HeadingTree(
            document_title="Test Document",
            title_page=1,
            sections=[
                HeadingNode(level=1, title="Introduction", page=1),
                HeadingNode(level=2, title="Background", page=2),
            ],
            total_pages=5,
            layout_type="single_column",
            confidence=0.9,
        )
        result = agent._format_heading_tree(tree)

        assert "Document Title: Test Document" in result
        assert "Layout: single_column" in result
        assert "Total Pages: 5" in result
        assert "# Introduction (page 1)" in result
        assert "## Background (page 2)" in result

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_heading_tree_with_section_numbers(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test heading tree formatting with section numbers."""
        agent = ExtractionAgent()
        tree = HeadingTree(
            document_title="Course Syllabus",
            sections=[
                HeadingNode(level=1, title="Course Overview", page=1, section_number="1"),
                HeadingNode(level=2, title="Objectives", page=1, section_number="1.1"),
                HeadingNode(level=2, title="Materials", page=2, section_number="1.2"),
            ],
            total_pages=3,
            layout_type="single_column",
        )
        result = agent._format_heading_tree(tree)

        assert "# 1 Course Overview (page 1)" in result
        assert "## 1.1 Objectives (page 1)" in result
        assert "## 1.2 Materials (page 2)" in result

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_heading_tree_nested_levels(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test heading tree formatting with nested levels."""
        agent = ExtractionAgent()
        tree = HeadingTree(
            document_title="Deep Document",
            sections=[
                HeadingNode(level=1, title="Chapter 1", page=1),
                HeadingNode(level=2, title="Section 1.1", page=1),
                HeadingNode(level=3, title="Subsection 1.1.1", page=2),
            ],
            total_pages=5,
            layout_type="single_column",
        )
        result = agent._format_heading_tree(tree)

        # Check indentation (level-1 spaces per level)
        assert "# Chapter 1" in result
        assert "  ## Section 1.1" in result
        assert "    ### Subsection 1.1.1" in result

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_heading_tree_two_column(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test heading tree with two-column layout."""
        agent = ExtractionAgent()
        tree = HeadingTree(
            document_title="Academic Paper",
            sections=[],
            total_pages=10,
            layout_type="two_column",
        )
        result = agent._format_heading_tree(tree)

        assert "Layout: two_column" in result


# =============================================================================
# Page Features Formatting Tests
# =============================================================================


@pytest.mark.unit
class TestPageFeaturesFormatting:
    """Tests for page features formatting."""

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_page_features_basic(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test basic page features formatting."""
        agent = ExtractionAgent()
        features = [
            PageFeatures(page_num=1, layout_type="single_column"),
            PageFeatures(page_num=2, layout_type="single_column"),
        ]
        result = agent._format_page_features(features)

        assert "Page-by-Page Content:" in result
        assert "Page 1:" in result
        assert "Page 2:" in result
        assert "[single_column]" in result

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_page_features_with_images(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test page features formatting with images."""
        agent = ExtractionAgent()
        features = [
            PageFeatures(page_num=1, has_images=True, image_count=2),
        ]
        result = agent._format_page_features(features)

        assert "2 image(s)" in result

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_page_features_with_tables(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test page features formatting with tables."""
        agent = ExtractionAgent()
        features = [
            PageFeatures(page_num=1, has_tables=True, table_count=3),
        ]
        result = agent._format_page_features(features)

        assert "3 table(s)" in result

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_format_page_features_with_all_content(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test page features formatting with all content types."""
        agent = ExtractionAgent()
        features = [
            PageFeatures(
                page_num=1,
                has_images=True,
                image_count=1,
                has_tables=True,
                table_count=2,
                has_lists=True,
                has_code_blocks=True,
                has_math=True,
                layout_type="mixed",
            ),
        ]
        result = agent._format_page_features(features)

        assert "1 image(s)" in result
        assert "2 table(s)" in result
        assert "lists" in result
        assert "code" in result
        assert "math" in result
        assert "[mixed]" in result


# =============================================================================
# Image Message Building Tests
# =============================================================================


@pytest.mark.unit
class TestImageMessageBuilding:
    """Tests for building image messages."""

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_build_image_messages_empty(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test building messages with no pages."""
        agent = ExtractionAgent()
        messages = agent._build_image_messages([])
        assert messages == []

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_build_image_messages_with_images(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test building messages with page images."""
        import base64

        agent = ExtractionAgent()
        test_image = base64.b64encode(b"fake image data").decode()
        pages = [
            PageData(page_num=1, image_base64=test_image),
            PageData(page_num=2, image_base64=test_image),
        ]
        messages = agent._build_image_messages(pages)

        # Should have alternating text and binary content
        assert len(messages) == 4  # 2 pages * (label + image)
        assert messages[0] == "[Page 1]"
        assert messages[2] == "[Page 2]"

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_build_image_messages_without_images(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test building messages with pages but no images."""
        agent = ExtractionAgent()
        pages = [
            PageData(page_num=1, image_base64=None),
            PageData(page_num=2, image_base64=None),
        ]
        messages = agent._build_image_messages(pages)

        # Should only have text labels (no binary content)
        assert len(messages) == 2
        assert messages[0] == "[Page 1]"
        assert messages[1] == "[Page 2]"


# =============================================================================
# Cost Calculation Tests
# =============================================================================


@pytest.mark.unit
class TestCostCalculation:
    """Tests for cost calculation with Haiku pricing."""

    def test_haiku_pricing_used(self) -> None:
        """Test Haiku pricing is correct."""
        # $1/1M input = 0.0001 cents/token
        # $5/1M output = 0.0005 cents/token
        assert HAIKU_PRICING.input_cost_per_token_cents == 0.0001
        assert HAIKU_PRICING.output_cost_per_token_cents == 0.0005

    def test_extraction_cost_calculation(self) -> None:
        """Test typical extraction cost calculation."""
        # Typical extraction: 52K input tokens (images), 8K output tokens
        input_tokens = 52000
        output_tokens = 8000

        cost = calculate_estimated_cost(input_tokens, output_tokens, HAIKU_PRICING)

        # Expected:
        # Input: 52000 * 0.0001 = 5.2 cents
        # Output: 8000 * 0.0005 = 4.0 cents
        # Total: 9.2 cents
        expected = (52000 * 0.0001) + (8000 * 0.0005)
        assert cost == pytest.approx(expected, rel=0.01)

    def test_cost_is_cheaper_than_sonnet(self) -> None:
        """Test Haiku extraction is cheaper than would be with Sonnet."""
        from src.shared.llm_cost import SONNET_PRICING

        input_tokens = 52000
        output_tokens = 8000

        haiku_cost = calculate_estimated_cost(input_tokens, output_tokens, HAIKU_PRICING)
        sonnet_cost = calculate_estimated_cost(
            input_tokens, output_tokens, SONNET_PRICING
        )

        # Haiku should be ~3x cheaper
        assert haiku_cost < sonnet_cost
        assert sonnet_cost / haiku_cost > 2.5  # At least 2.5x more expensive


# =============================================================================
# Extract Method Tests
# =============================================================================


@pytest.mark.unit
class TestExtractMethod:
    """Tests for the extract method."""

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_extract_raises_on_empty_pages(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test extract raises ValueError with no pages."""
        agent = ExtractionAgent()
        manifest = DocumentManifest(
            job_id="test-job",
            total_pages=1,
            heading_tree_json=HeadingTree(
                document_title="Test",
                sections=[],
                total_pages=1,
            ).model_dump_json(),
        )

        with pytest.raises(ValueError, match="No pages provided"):
            import asyncio

            asyncio.get_event_loop().run_until_complete(
                agent.extract([], manifest, "test-job")
            )

    @pytest.mark.asyncio
    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    async def test_extract_returns_extraction_output(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test extract returns ExtractionOutput with Reasoned[T] fields."""
        agent = ExtractionAgent()

        heading_tree = HeadingTree(
            document_title="Test Doc",
            sections=[HeadingNode(level=1, title="Intro", page=1)],
            total_pages=2,
            layout_type="two_column",
        )

        manifest = DocumentManifest(
            job_id="test-job",
            document_title="Test Doc",
            document_type="syllabus",
            total_pages=2,
            heading_tree_json=heading_tree.model_dump_json(),
            page_features=[PageFeatures(page_num=1), PageFeatures(page_num=2)],
            analysis_notes="Test notes",
        )

        # Mock the agent.run call with new output format
        mock_result = MagicMock()
        mock_result.output = _create_extraction_output(
            markdown="# Test Doc\n\n## Intro\n\nContent",
            confidence_value=0.9,
            confidence_reasoning="Clear images, all content visible. 0.9.",
            reading_order_value=True,
            reading_order_reasoning="Two-column pages, left then right. True.",
            pages_transcribed=[1, 2],
            transcription_notes="",
        )
        mock_result.usage.return_value = MagicMock(
            request_tokens=1000, response_tokens=500
        )

        with (
            patch.object(agent, "_get_agent") as mock_get_agent,
            patch.object(agent, "_log_reasoning_corpus", new_callable=AsyncMock),
        ):
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            import base64

            test_image = base64.b64encode(b"fake").decode()
            pages = [PageData(page_num=1, image_base64=test_image)]

            output, usage = await agent.extract(pages, manifest, "test-job")

            # Verify output is ExtractionOutput with Reasoned[T] fields
            assert output.markdown == "# Test Doc\n\n## Intro\n\nContent"
            assert output.confidence.value == 0.9
            assert output.confidence.reasoning == "Clear images, all content visible. 0.9."
            assert output.reading_order_followed.value is True
            assert output.pages_transcribed == [1, 2]
            assert usage.input_tokens == 1000
            assert usage.output_tokens == 500


# =============================================================================
# Integration Tests (with mocked Bedrock)
# =============================================================================


@pytest.mark.unit
class TestExtractionAgentIntegration:
    """Integration tests with mocked Bedrock model."""

    @pytest.mark.asyncio
    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    async def test_full_extraction_flow(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test full extraction flow with mocked model."""
        agent = ExtractionAgent()

        # Create realistic manifest
        heading_tree = HeadingTree(
            document_title="CS 101 Syllabus",
            sections=[
                HeadingNode(level=1, title="Course Information", page=1),
                HeadingNode(level=2, title="Instructor", page=1),
                HeadingNode(level=2, title="Schedule", page=2),
            ],
            total_pages=3,
            layout_type="single_column",
        )

        manifest = DocumentManifest(
            job_id="job-123",
            document_title="CS 101 Syllabus",
            document_type="syllabus",
            total_pages=3,
            heading_tree_json=heading_tree.model_dump_json(),
            page_features=[
                PageFeatures(page_num=1, has_images=False),
                PageFeatures(page_num=2, has_tables=True, table_count=1),
                PageFeatures(page_num=3),
            ],
            required_agents=["tables"],
            analysis_notes="Simple syllabus with schedule table.",
        )

        # Mock the model response
        expected_markdown = """# CS 101 Syllabus

## Course Information

### Instructor

Dr. Jane Smith
Office: 123 Science Hall

### Schedule

| Week | Topic | Assignment |
|------|-------|------------|
| 1 | Introduction | Read Ch 1 |
| 2 | Variables | HW 1 |
"""

        mock_result = MagicMock()
        mock_result.output = _create_extraction_output(
            markdown=expected_markdown,
            confidence_value=0.88,
            confidence_reasoning="All 3 pages clear, table on page 2 intact. 0.88.",
            reading_order_value=True,
            reading_order_reasoning="Single-column layout throughout. True.",
            pages_transcribed=[1, 2, 3],
            transcription_notes="Table on page 2 transcribed successfully.",
        )
        mock_result.usage.return_value = MagicMock(
            request_tokens=15000, response_tokens=2000
        )

        with (
            patch.object(agent, "_get_agent") as mock_get_agent,
            patch.object(agent, "_log_reasoning_corpus", new_callable=AsyncMock),
        ):
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            import base64

            test_image = base64.b64encode(b"fake image").decode()
            pages = [
                PageData(page_num=1, image_base64=test_image),
                PageData(page_num=2, image_base64=test_image),
                PageData(page_num=3, image_base64=test_image),
            ]

            output, usage = await agent.extract(pages, manifest, "job-123")

            # Verify output
            assert output.markdown == expected_markdown
            assert output.confidence.value == 0.88
            assert output.reading_order_followed.value is True
            assert output.pages_transcribed == [1, 2, 3]

            # Verify usage tracking
            assert usage.input_tokens == 15000
            assert usage.output_tokens == 2000
            assert usage.total_tokens == 17000

            # Verify cost calculation (Haiku pricing)
            expected_cost = calculate_estimated_cost(15000, 2000, HAIKU_PRICING)
            assert usage.estimated_cost_cents == pytest.approx(expected_cost, rel=0.01)

    @pytest.mark.asyncio
    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    async def test_extraction_with_image_placeholders(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test extraction produces correct image placeholder format."""
        agent = ExtractionAgent()

        heading_tree = HeadingTree(
            document_title="Image Document",
            sections=[],
            total_pages=2,
        )

        manifest = DocumentManifest(
            job_id="job-456",
            total_pages=2,
            heading_tree_json=heading_tree.model_dump_json(),
            page_features=[
                PageFeatures(page_num=1, has_images=True, image_count=2),
                PageFeatures(page_num=2, has_images=True, image_count=1),
            ],
        )

        # Expected markdown with image placeholders
        expected_markdown = """# Image Document

Some text here.

![TODO: describe](image-page-1-1.png)

More text.

![TODO: describe](image-page-1-2.png)

Page 2 content.

![TODO: describe](image-page-2-1.png)
"""

        mock_result = MagicMock()
        mock_result.output = _create_extraction_output(
            markdown=expected_markdown,
            confidence_value=0.85,
            confidence_reasoning="Images placed correctly, text clear. 0.85.",
            reading_order_value=True,
            reading_order_reasoning="Single-column layout. True.",
            pages_transcribed=[1, 2],
        )
        mock_result.usage.return_value = MagicMock(
            request_tokens=10000, response_tokens=500
        )

        with (
            patch.object(agent, "_get_agent") as mock_get_agent,
            patch.object(agent, "_log_reasoning_corpus", new_callable=AsyncMock),
        ):
            mock_agent = AsyncMock()
            mock_agent.run = AsyncMock(return_value=mock_result)
            mock_get_agent.return_value = mock_agent

            import base64

            test_image = base64.b64encode(b"fake").decode()
            pages = [
                PageData(page_num=1, image_base64=test_image),
                PageData(page_num=2, image_base64=test_image),
            ]

            output, _ = await agent.extract(pages, manifest, "job-456")

            # Verify placeholder format
            assert "![TODO: describe](image-page-1-1.png)" in output.markdown
            assert "![TODO: describe](image-page-1-2.png)" in output.markdown
            assert "![TODO: describe](image-page-2-1.png)" in output.markdown


# =============================================================================
# Dynamic Instructions Tests
# =============================================================================


@pytest.mark.unit
class TestDynamicInstructions:
    """Tests for dynamic instruction registration."""

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_document_type_hints_include_research_paper(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test research_paper document type hint exists."""
        agent = ExtractionAgent()
        assert "research_paper" in agent.DOCUMENT_TYPE_HINTS
        assert "two-column layout" in agent.DOCUMENT_TYPE_HINTS["research_paper"].lower()

    @patch("src.agents.core.yaml.safe_load", return_value={"system_prompt": "test", "user_prompt": "test"})
    @patch("builtins.open", create=True)
    def test_all_document_types_have_hints(self, mock_open: MagicMock, mock_yaml: MagicMock) -> None:
        """Test all expected document types have hints."""
        agent = ExtractionAgent()
        expected_types = ["syllabus", "exam", "lecture_notes", "assignment", "research_paper"]
        for doc_type in expected_types:
            assert doc_type in agent.DOCUMENT_TYPE_HINTS, f"Missing hint for {doc_type}"
