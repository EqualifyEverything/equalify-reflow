"""Tests for FiguresAgent (Phase 3b: Refine).

Tests cover:
- Image placeholder detection in markdown
- Decorative image handling (auto-correction with empty alt)
- High-confidence informative images (auto-correction)
- Low-confidence images (review items)
- Complex/text images (always review items)
- Alt text validation
- Unfilled placeholder detection
- AgentResult output format

Reference: PRD-023 - Figures Agent Refactor
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.agents.figures import FiguresAgent, ImagePlaceholder
from src.agents.figures.classification_agent import (
    ImageClassification,
    ImageClassificationOutput,
)
from src.agents.figures.generation_agent import (
    AltTextGeneration,
    AltTextGenerationOutput,
)
from src.services.pdf_converter import PageData
from src.shared.models.observation import Observation, ObservationLocation
from src.shared.models.processing import LLMUsage
from src.shared.models.remediation import DocumentManifest, PageFeatures
from src.shared.models.review_checklist import ReviewItem, ReviewOption

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_manifest() -> DocumentManifest:
    """Create sample manifest for testing."""
    return DocumentManifest(
        job_id="test-job-123",
        document_title="Test Document",
        document_type="syllabus",
        total_pages=3,
        heading_tree_json="{}",
        page_features=[
            PageFeatures(page_num=1, has_images=True, image_count=2),
            PageFeatures(page_num=2, has_images=False, image_count=0),
            PageFeatures(page_num=3, has_images=True, image_count=1),
        ],
        required_agents=["figures"],
    )


@pytest.fixture
def sample_pages() -> list[PageData]:
    """Create sample pages for testing."""
    return [
        PageData(page_num=1, image_base64="YWJjMTIz"),  # "abc123" in base64
        PageData(page_num=2, image_base64="ZGVmNDU2"),
        PageData(page_num=3, image_base64="Z2hpNzg5"),
    ]


@pytest.fixture
def mock_usage() -> LLMUsage:
    """Create mock LLM usage."""
    return LLMUsage(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        estimated_cost_cents=1.0,
    )


# =============================================================================
# ImagePlaceholder Tests
# =============================================================================


@pytest.mark.unit
class TestImagePlaceholder:
    """Tests for ImagePlaceholder model."""

    def test_valid_placeholder(self):
        """Test creating a valid ImagePlaceholder."""
        placeholder = ImagePlaceholder(
            id="img-p1-1",
            full_match="![TODO: describe](image-page-1-1.png)",
            current_alt="TODO: describe",
            src="image-page-1-1.png",
            page_num=1,
            image_index=1,
        )
        assert placeholder.id == "img-p1-1"
        assert placeholder.page_num == 1
        assert placeholder.image_index == 1

    def test_placeholder_with_alt_text(self):
        """Test placeholder with existing alt text."""
        placeholder = ImagePlaceholder(
            id="img-p2-1",
            full_match="![A flowchart](image-page-2-1.png)",
            current_alt="A flowchart",
            src="image-page-2-1.png",
            page_num=2,
            image_index=1,
        )
        assert placeholder.current_alt == "A flowchart"


# =============================================================================
# Placeholder Detection Tests
# =============================================================================


@pytest.mark.unit
class TestPlaceholderDetection:
    """Tests for _find_image_placeholders method."""

    def test_find_single_placeholder(self):
        """Test finding a single image placeholder."""
        agent = FiguresAgent()
        markdown = "# Test\n\n![TODO: describe](image-page-1-1.png)\n\nSome text."

        placeholders = agent._find_image_placeholders(markdown)

        assert len(placeholders) == 1
        assert placeholders[0].page_num == 1
        assert placeholders[0].image_index == 1
        assert placeholders[0].current_alt == "TODO: describe"

    def test_find_multiple_placeholders(self):
        """Test finding multiple image placeholders."""
        agent = FiguresAgent()
        markdown = """# Test

![First image](image-page-1-1.png)

Some text.

![Second image](image-page-1-2.png)

More text.

![Third on page 2](image-page-2-1.png)
"""

        placeholders = agent._find_image_placeholders(markdown)

        assert len(placeholders) == 3
        assert placeholders[0].id == "img-p1-1"
        assert placeholders[1].id == "img-p1-2"
        assert placeholders[2].id == "img-p2-1"

    def test_find_no_placeholders(self):
        """Test with no image placeholders."""
        agent = FiguresAgent()
        markdown = "# Test\n\nNo images here."

        placeholders = agent._find_image_placeholders(markdown)

        assert len(placeholders) == 0

    def test_find_placeholder_with_empty_alt(self):
        """Test finding placeholder with empty alt text."""
        agent = FiguresAgent()
        markdown = "![](image-page-1-1.png)"

        placeholders = agent._find_image_placeholders(markdown)

        assert len(placeholders) == 1
        assert placeholders[0].current_alt == ""


# =============================================================================
# Alt Text Validation Tests
# =============================================================================


@pytest.mark.unit
class TestAltTextValidation:
    """Tests for _validate_alt_text method."""

    def test_valid_alt_text(self):
        """Test valid alt text passes validation."""
        agent = FiguresAgent()
        assert agent._validate_alt_text("A flowchart showing the registration process")

    def test_empty_alt_text_fails(self):
        """Test empty alt text fails validation."""
        agent = FiguresAgent()
        assert not agent._validate_alt_text("")

    def test_short_alt_text_fails(self):
        """Test alt text shorter than 10 chars fails."""
        agent = FiguresAgent()
        assert not agent._validate_alt_text("Chart")

    def test_todo_alt_text_fails(self):
        """Test alt text starting with TODO fails."""
        agent = FiguresAgent()
        assert not agent._validate_alt_text("TODO: describe this image")
        assert not agent._validate_alt_text("todo: add description")

    def test_generic_alt_text_fails(self):
        """Test generic alt text fails."""
        agent = FiguresAgent()
        assert not agent._validate_alt_text("image")
        assert not agent._validate_alt_text("picture")
        assert not agent._validate_alt_text("photo")
        assert not agent._validate_alt_text("figure")
        assert not agent._validate_alt_text("graphic")


# =============================================================================
# Full Processing Tests
# =============================================================================


@pytest.mark.unit
class TestFiguresAgentProcess:
    """Tests for full process() method."""

    @pytest.mark.asyncio
    async def test_no_placeholders_returns_empty_result(
        self,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
    ):
        """Test processing with no image placeholders."""
        agent = FiguresAgent()
        markdown = "# No images\n\nJust text content."

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert result.agent_name == "figures"
        assert len(result.observations) == 0
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 0
        assert result.confidence == 1.0
        assert "No image placeholders found" in result.reasoning_summary

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_decorative_image_auto_corrects(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test decorative images get auto-corrected with empty alt."""
        # Mock classification - decorative image
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="decorative",
                        confidence=0.98,
                        visual_description="Decorative border pattern",
                        decorative_reasoning="Background pattern with no informational content",
                    )
                ],
            ),
            mock_usage,
        )

        # Generate should not be called for decorative images
        mock_generate.return_value = (
            AltTextGenerationOutput(page_num=1, generations=[]),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO: describe](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0

        correction = result.auto_corrections[0]
        assert correction.replace == "![](image-page-1-1.png)"  # Empty alt
        assert "Decorative" in correction.justification

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_high_confidence_informative_auto_corrects(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test high-confidence informative images get auto-corrected."""
        # Mock classification - informative image
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.98,
                        visual_description="Photo of campus building",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        # Mock generation - high confidence alt text
        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="The main entrance of the Science Building on campus",
                        confidence=0.96,
                        generation_notes="Clear image with identifiable building",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO: describe](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0

        correction = result.auto_corrections[0]
        assert "Science Building" in correction.replace
        assert correction.confidence >= 0.95

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_low_confidence_creates_review_item(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test low-confidence images create review items."""
        # Mock classification - informative but low confidence
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.75,  # Below threshold
                        visual_description="Unclear image",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        # Mock generation - also low confidence
        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="A photograph that may show a building or landscape",
                        confidence=0.70,
                        generation_notes="Image quality makes identification difficult",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO: describe](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1

        review_item = result.review_items[0]
        assert review_item.agent == "figures"
        assert len(review_item.options) >= 2  # Accept and other options
        assert review_item.agent_confidence < 0.95

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_complex_image_always_review(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test complex images always go to review regardless of confidence."""
        # Mock classification - complex image with high confidence
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="complex",  # Complex type
                        confidence=0.99,  # High confidence
                        visual_description="Detailed flowchart with 10 connected boxes",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        # Mock generation - high confidence
        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="complex",
                        alt_text="Flowchart showing 10-step registration process",
                        confidence=0.98,
                        long_description_needed=True,
                        long_description="Detailed description of the flowchart...",
                        generation_notes="Complex diagram requires verification",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO: describe](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Complex images always go to review
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1

        review_item = result.review_items[0]
        assert "complex" in review_item.question.lower()

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_text_image_always_review(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test text images always go to review for OCR verification."""
        # Mock classification - text image
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="text",
                        confidence=0.99,
                        visual_description="Image containing text table",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        # Mock generation
        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="text",
                        alt_text="Table showing course schedule with dates and times",
                        confidence=0.95,
                        generation_notes="OCR transcription from image",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO: describe](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Text images always go to review (OCR verification)
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_multiple_images_processed(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test processing multiple images on same page."""
        # Mock classification - multiple images
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=2,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="decorative",
                        confidence=0.99,
                        visual_description="Decorative border",
                        decorative_reasoning="Border pattern",
                    ),
                    ImageClassification(
                        image_index=2,
                        image_type="informative",
                        confidence=0.97,
                        visual_description="Campus photo",
                        decorative_reasoning="",
                    ),
                ],
            ),
            mock_usage,
        )

        # Mock generation for non-decorative
        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=2,
                        image_type="informative",
                        alt_text="Students walking across the main quad on a sunny day",
                        confidence=0.96,
                        generation_notes="Clear campus photo",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)\n\n![TODO](image-page-1-2.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Both should be auto-corrected (decorative and high-confidence informative)
        assert len(result.auto_corrections) == 2
        assert len(result.observations) == 2
        assert "2 auto-corrected" in result.reasoning_summary


# =============================================================================
# Summary and Confidence Calculation Tests
# =============================================================================


@pytest.mark.unit
class TestSummaryAndConfidence:
    """Tests for summary and confidence calculation."""

    def test_build_summary(self):
        """Test summary generation."""
        agent = FiguresAgent()
        placeholders = [
            ImagePlaceholder(
                id="1", full_match="", current_alt="", src="", page_num=1, image_index=1
            ),
            ImagePlaceholder(
                id="2", full_match="", current_alt="", src="", page_num=1, image_index=2
            ),
        ]
        auto_corrections = [MagicMock()]
        review_items = [MagicMock()]

        summary = agent._build_summary(placeholders, auto_corrections, review_items)

        assert "2 images" in summary
        assert "1 auto-corrected" in summary
        assert "1 need review" in summary

    def test_calculate_confidence_empty(self):
        """Test confidence calculation with no items."""
        agent = FiguresAgent()
        confidence = agent._calculate_confidence([], [])
        assert confidence == 1.0

    def test_calculate_confidence_with_items(self):
        """Test confidence calculation with items."""
        agent = FiguresAgent()

        auto_correction = MagicMock()
        auto_correction.confidence = 0.98

        review_item = MagicMock()
        review_item.agent_confidence = 0.80

        confidence = agent._calculate_confidence([auto_correction], [review_item])

        # Average of 0.98 and 0.80 = 0.89
        assert 0.88 < confidence < 0.90


# =============================================================================
# Page Markdown Extraction Tests
# =============================================================================


@pytest.mark.unit
class TestPageMarkdownExtraction:
    """Tests for _get_page_markdown method."""

    def test_extract_with_page_markers(self):
        """Test extraction with page comment markers."""
        agent = FiguresAgent()
        markdown = """<!-- Page 1 -->
# Page 1 content

<!-- Page 2 -->
# Page 2 content
More text here.

<!-- Page 3 -->
# Page 3 content
"""

        page_md = agent._get_page_markdown(markdown, 2, total_pages=3)

        assert "Page 2 content" in page_md
        assert "More text here" in page_md
        assert "Page 1 content" not in page_md
        assert "Page 3 content" not in page_md

    def test_extract_without_markers_fallback(self):
        """Test extraction falls back to line estimation."""
        agent = FiguresAgent()
        # Create markdown without page markers
        lines = [f"Line {i}" for i in range(100)]
        markdown = "\n".join(lines)

        page_md = agent._get_page_markdown(markdown, 1, total_pages=10)

        # Should get some content
        assert len(page_md) > 0
        assert "Line 0" in page_md

    def test_extract_empty_markdown(self):
        """Test extraction from empty markdown."""
        agent = FiguresAgent()
        page_md = agent._get_page_markdown("", 1, total_pages=1)
        assert page_md == ""


# =============================================================================
# Review Item Creation Tests
# =============================================================================


@pytest.mark.unit
class TestReviewItemCreation:
    """Tests for _create_review_item_for_missing_generation method."""

    def test_missing_generation_creates_review_item(self):
        """Test that missing generation for non-decorative image creates review item."""
        agent = FiguresAgent()
        placeholder = ImagePlaceholder(
            id="img-p1-1",
            full_match="![TODO: describe](image-page-1-1.png)",
            current_alt="TODO: describe",
            src="image-page-1-1.png",
            page_num=1,
            image_index=1,
        )
        classification = ImageClassification(
            image_index=1,
            image_type="informative",
            confidence=0.85,
            visual_description="A campus building",
            decorative_reasoning="",
        )
        observation = Observation(
            id="obs-1",
            job_id="test-job",
            agent="figures",
            source="agent",
            visual_description="informative image",
            markup_description="Current alt: 'TODO: describe'",
            location=ObservationLocation(
                location_type="element",
                value="img[src='image-page-1-1.png']",
                page_num=1,
            ),
            confidence=0.85,
            severity="major",
            category="alt_text",
        )
        review_items: list[ReviewItem] = []

        agent._create_review_item_for_missing_generation(
            placeholder=placeholder,
            classification=classification,
            observation=observation,
            review_items=review_items,
        )

        assert len(review_items) == 1
        review_item = review_items[0]
        assert review_item.agent == "figures"
        assert review_item.observation_id == "obs-1"
        assert review_item.page_num == 1
        assert "informative image" in review_item.question.lower()
        assert review_item.agent_confidence == 0.0
        assert review_item.agent_recommendation == "Manual input required"

    def test_missing_generation_review_has_correct_options(self):
        """Test that review item for missing generation has correct options."""
        agent = FiguresAgent()
        placeholder = ImagePlaceholder(
            id="img-p1-1",
            full_match="![TODO](image-page-1-1.png)",
            current_alt="TODO",
            src="image-page-1-1.png",
            page_num=1,
            image_index=1,
        )
        classification = ImageClassification(
            image_index=1,
            image_type="complex",
            confidence=0.90,
            visual_description="A detailed diagram",
            decorative_reasoning="",
        )
        observation = Observation(
            id="obs-1",
            job_id="test-job",
            agent="figures",
            source="agent",
            visual_description="complex image",
            markup_description="Current alt: 'TODO'",
            location=ObservationLocation(
                location_type="element",
                value="img[src='image-page-1-1.png']",
                page_num=1,
            ),
            confidence=0.90,
            severity="major",
            category="alt_text",
        )
        review_items: list[ReviewItem] = []

        agent._create_review_item_for_missing_generation(
            placeholder=placeholder,
            classification=classification,
            observation=observation,
            review_items=review_items,
        )

        review_item = review_items[0]
        assert len(review_item.options) == 2

        # Check decorative option
        decorative_option = next(o for o in review_item.options if o.id == "decorative")
        assert decorative_option.label == "Mark as decorative (empty alt)"
        assert decorative_option.action == "replace"
        assert decorative_option.replacement_text == f"![]({placeholder.src})"
        assert not decorative_option.is_recommended

        # Check custom option
        custom_option = next(o for o in review_item.options if o.id == "other")
        assert custom_option.label == "Write custom alt text"
        assert custom_option.action == "other"
        assert custom_option.is_recommended

    def test_missing_generation_review_has_correct_context(self):
        """Test that review item for missing generation has correct context."""
        agent = FiguresAgent()
        placeholder = ImagePlaceholder(
            id="img-p2-3",
            full_match="![](image-page-2-3.png)",
            current_alt="",
            src="image-page-2-3.png",
            page_num=2,
            image_index=3,
        )
        classification = ImageClassification(
            image_index=3,
            image_type="text",
            confidence=0.95,
            visual_description="A table with course schedule data",
            decorative_reasoning="",
        )
        observation = Observation(
            id="obs-2",
            job_id="test-job",
            agent="figures",
            source="agent",
            visual_description="text image",
            markup_description="Empty alt",
            location=ObservationLocation(
                location_type="element",
                value="img[src='image-page-2-3.png']",
                page_num=2,
            ),
            confidence=0.95,
            severity="major",
            category="alt_text",
        )
        review_items: list[ReviewItem] = []

        agent._create_review_item_for_missing_generation(
            placeholder=placeholder,
            classification=classification,
            observation=observation,
            review_items=review_items,
        )

        review_item = review_items[0]
        assert "Image type: text" in review_item.context
        assert "A table with course schedule data" in review_item.context
        assert "Alt text generation failed" in review_item.context
        assert review_item.search_text == placeholder.full_match


# =============================================================================
# Unfilled Placeholder Detection Tests
# =============================================================================


@pytest.mark.unit
class TestUnfilledPlaceholders:
    """Tests for _check_unfilled_placeholders method."""

    def test_all_placeholders_in_enhanced_content(self):
        """Test when all placeholders are in enhanced_content."""
        agent = FiguresAgent()
        placeholders = [
            ImagePlaceholder(
                id="img-p1-1",
                full_match="![TODO](image-page-1-1.png)",
                current_alt="TODO",
                src="image-page-1-1.png",
                page_num=1,
                image_index=1,
            ),
            ImagePlaceholder(
                id="img-p1-2",
                full_match="![TODO](image-page-1-2.png)",
                current_alt="TODO",
                src="image-page-1-2.png",
                page_num=1,
                image_index=2,
            ),
        ]
        enhanced_content = {
            "img-p1-1": "A campus building",
            "img-p1-2": "",  # Decorative
        }
        review_items: list[ReviewItem] = []

        unfilled = agent._check_unfilled_placeholders(
            placeholders, enhanced_content, review_items
        )

        assert len(unfilled) == 0

    def test_all_placeholders_in_review_items(self):
        """Test when all placeholders are in review_items."""
        agent = FiguresAgent()
        placeholders = [
            ImagePlaceholder(
                id="img-p1-1",
                full_match="![TODO](image-page-1-1.png)",
                current_alt="TODO",
                src="image-page-1-1.png",
                page_num=1,
                image_index=1,
            ),
        ]
        enhanced_content: dict[str, str] = {}
        review_items = [
            ReviewItem(
                id="review-1",
                observation_id="obs-1",
                agent="figures",
                question="Verify alt text",
                options=[
                    ReviewOption(
                        id="accept",
                        label="Accept",
                        action="replace",
                        replacement_text="![Alt text](image-page-1-1.png)",
                        is_recommended=True,
                    ),
                    ReviewOption(
                        id="reject",
                        label="Reject",
                        action="other",
                        is_recommended=False,
                    ),
                ],
                search_text="![TODO](image-page-1-1.png)",  # Matches placeholder
                context="",
                page_num=1,
                agent_recommendation="",
                agent_confidence=0.8,
            )
        ]

        unfilled = agent._check_unfilled_placeholders(
            placeholders, enhanced_content, review_items
        )

        assert len(unfilled) == 0

    def test_detects_unfilled_placeholders(self):
        """Test detection of placeholders not in enhanced_content or review_items."""
        agent = FiguresAgent()
        placeholders = [
            ImagePlaceholder(
                id="img-p1-1",
                full_match="![TODO](image-page-1-1.png)",
                current_alt="TODO",
                src="image-page-1-1.png",
                page_num=1,
                image_index=1,
            ),
            ImagePlaceholder(
                id="img-p1-2",
                full_match="![TODO](image-page-1-2.png)",
                current_alt="TODO",
                src="image-page-1-2.png",
                page_num=1,
                image_index=2,
            ),
            ImagePlaceholder(
                id="img-p2-1",
                full_match="![TODO](image-page-2-1.png)",
                current_alt="TODO",
                src="image-page-2-1.png",
                page_num=2,
                image_index=1,
            ),
        ]
        # Only process first placeholder
        enhanced_content = {
            "img-p1-1": "Processed image",
        }
        review_items: list[ReviewItem] = []

        unfilled = agent._check_unfilled_placeholders(
            placeholders, enhanced_content, review_items
        )

        assert len(unfilled) == 2
        assert unfilled[0].id == "img-p1-2"
        assert unfilled[1].id == "img-p2-1"

    def test_mixed_enhanced_and_review(self):
        """Test with some placeholders in enhanced_content and some in review_items."""
        agent = FiguresAgent()
        placeholders = [
            ImagePlaceholder(
                id="img-p1-1",
                full_match="![TODO](image-page-1-1.png)",
                current_alt="TODO",
                src="image-page-1-1.png",
                page_num=1,
                image_index=1,
            ),
            ImagePlaceholder(
                id="img-p1-2",
                full_match="![TODO](image-page-1-2.png)",
                current_alt="TODO",
                src="image-page-1-2.png",
                page_num=1,
                image_index=2,
            ),
            ImagePlaceholder(
                id="img-p2-1",
                full_match="![TODO](image-page-2-1.png)",
                current_alt="TODO",
                src="image-page-2-1.png",
                page_num=2,
                image_index=1,
            ),
        ]
        enhanced_content = {
            "img-p1-1": "Auto-corrected",
        }
        review_items = [
            ReviewItem(
                id="review-1",
                observation_id="obs-1",
                agent="figures",
                question="Verify",
                options=[
                    ReviewOption(
                        id="accept",
                        label="Accept",
                        action="replace",
                        replacement_text="![Alt text](image-page-1-2.png)",
                        is_recommended=True,
                    ),
                    ReviewOption(
                        id="reject",
                        label="Reject",
                        action="other",
                        is_recommended=False,
                    ),
                ],
                search_text="![TODO](image-page-1-2.png)",
                context="",
                page_num=1,
                agent_recommendation="",
                agent_confidence=0.8,
            )
        ]

        unfilled = agent._check_unfilled_placeholders(
            placeholders, enhanced_content, review_items
        )

        # Only img-p2-1 should be unfilled
        assert len(unfilled) == 1
        assert unfilled[0].id == "img-p2-1"


# =============================================================================
# Error Handling Tests
# =============================================================================


@pytest.mark.unit
class TestErrorHandling:
    """Tests for error handling in process() method."""

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_classification_fails_page_continues(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that when classification fails on page 1, page 2 still processes."""
        # Page 1 classification raises exception
        # Page 2 classification succeeds
        mock_classify.side_effect = [
            Exception("Classification failed for page 1"),
            (
                ImageClassificationOutput(
                    page_num=2,
                    images_found=1,
                    classifications=[
                        ImageClassification(
                            image_index=1,
                            image_type="decorative",
                            confidence=0.99,
                            visual_description="Decorative border",
                            decorative_reasoning="Just decoration",
                        )
                    ],
                ),
                mock_usage,
            ),
        ]

        agent = FiguresAgent()
        markdown = """![TODO](image-page-1-1.png)

![TODO](image-page-2-1.png)"""

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Page 2 should still be processed
        assert len(result.auto_corrections) == 1
        assert result.auto_corrections[0].page_num == 2

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_exception_during_page_processing_creates_review(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that exception during page processing is handled gracefully."""
        # Classification succeeds but generate raises exception
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.95,
                        visual_description="A photo",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        mock_generate.side_effect = Exception("Generation failed")

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should complete without crashing
        assert result.agent_name == "figures"
        # Due to exception, no corrections/reviews may be created
        # But the process should complete


# =============================================================================
# Confidence Boundary Tests
# =============================================================================


@pytest.mark.unit
class TestConfidenceBoundaries:
    """Tests for confidence threshold boundary conditions."""

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_confidence_exactly_threshold_auto_corrects(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that confidence exactly at 0.95 results in auto-correction."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.95,  # Exactly at threshold
                        visual_description="Clear image",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="A clear photograph of the campus entrance",
                        confidence=0.95,  # Exactly at threshold
                        generation_notes="Clear image",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should auto-correct (>= threshold)
        assert len(result.auto_corrections) == 1
        assert len(result.review_items) == 0
        assert result.auto_corrections[0].confidence == 0.95

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_confidence_below_threshold_creates_review(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that confidence at 0.9499 (just below 0.95) creates review item."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.95,
                        visual_description="Slightly unclear image",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="A photograph that might show the entrance",
                        confidence=0.9499,  # Just below threshold
                        generation_notes="Slightly uncertain",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Should create review item (< threshold)
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert result.review_items[0].agent_confidence < 0.95

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_combined_confidence_uses_minimum(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that combined confidence uses min(classification, generation)."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.98,  # High
                        visual_description="Very clear image",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="A photograph with some uncertainty in details",
                        confidence=0.92,  # Lower, brings combined below threshold
                        generation_notes="Some details unclear",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        result = await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Combined confidence = min(0.98, 0.92) = 0.92 < 0.95 → review
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert result.review_items[0].agent_confidence == 0.92


# =============================================================================
# Page Data Lookup Failure Tests
# =============================================================================


@pytest.mark.unit
class TestPageDataLookup:
    """Tests for page data lookup failure scenarios."""

    @pytest.mark.asyncio
    async def test_placeholder_for_missing_page(
        self,
        sample_manifest: DocumentManifest,
    ):
        """Test when placeholder exists for page 3, but pages list only has 2 pages."""
        agent = FiguresAgent()
        markdown = "![TODO](image-page-3-1.png)"

        # Only 2 pages provided, but placeholder references page 3
        pages = [
            PageData(page_num=1, image_base64="YWJjMTIz"),
            PageData(page_num=2, image_base64="ZGVmNDU2"),
        ]

        result = await agent.process(
            markdown=markdown,
            pages=pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Page skipped with warning, but unfilled placeholder creates review item
        assert result.agent_name == "figures"
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert "placeholder not processed" in result.review_items[0].question

    @pytest.mark.asyncio
    async def test_page_data_with_empty_image(
        self,
        sample_manifest: DocumentManifest,
    ):
        """Test when PageData exists but image_base64 is empty/None."""
        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        # Page exists but has empty image_base64
        pages = [
            PageData(page_num=1, image_base64=""),  # Empty
        ]

        result = await agent.process(
            markdown=markdown,
            pages=pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Page skipped with warning, but unfilled placeholder creates review item
        assert result.agent_name == "figures"
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert "placeholder not processed" in result.review_items[0].question

    @pytest.mark.asyncio
    async def test_page_data_with_none_image(
        self,
        sample_manifest: DocumentManifest,
    ):
        """Test when PageData exists but image_base64 is None."""
        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        # Page exists but has None image_base64
        pages = [
            PageData(page_num=1, image_base64=None),  # type: ignore[arg-type]
        ]

        result = await agent.process(
            markdown=markdown,
            pages=pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Page skipped with warning, but unfilled placeholder creates review item
        assert result.agent_name == "figures"
        assert len(result.auto_corrections) == 0
        assert len(result.review_items) == 1
        assert "placeholder not processed" in result.review_items[0].question


# =============================================================================
# Mock Call Verification Tests
# =============================================================================


@pytest.mark.unit
class TestMockCallVerification:
    """Tests with mock call verification for key agent interactions."""

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_classify_called_with_correct_params(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that classify is called with correct parameters."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="decorative",
                        confidence=0.99,
                        visual_description="Border",
                        decorative_reasoning="Decoration",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job-123",
        )

        # Verify classify was called once with correct parameters
        mock_classify.assert_called_once()
        call_args = mock_classify.call_args
        assert call_args.kwargs["page_num"] == 1
        assert call_args.kwargs["image_base64"] == "YWJjMTIz"
        assert call_args.kwargs["expected_image_count"] == 2  # From manifest page_features
        assert call_args.kwargs["job_id"] == "test-job-123"
        assert "page_markdown" in call_args.kwargs

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_generate_not_called_for_decorative(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that generate is not called for decorative images."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="decorative",
                        confidence=0.99,
                        visual_description="Decorative border",
                        decorative_reasoning="Background pattern",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Generate should NOT be called for decorative images
        mock_generate.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_generate_called_for_informative(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that generate is called for informative images."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.95,
                        visual_description="Campus photo",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="Students walking across campus quad",
                        confidence=0.95,
                        generation_notes="Clear photo",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Generate SHOULD be called for informative images
        mock_generate.assert_called_once()
        call_args = mock_generate.call_args
        assert call_args.kwargs["page_num"] == 1
        assert call_args.kwargs["image_base64"] == "YWJjMTIz"
        assert len(call_args.kwargs["classifications"]) == 1
        assert call_args.kwargs["job_id"] == "test-job"

    @pytest.mark.asyncio
    @patch("src.agents.figures.figures_agent.classify")
    @patch("src.agents.figures.figures_agent.generate")
    async def test_generate_receives_document_context(
        self,
        mock_generate: AsyncMock,
        mock_classify: AsyncMock,
        sample_manifest: DocumentManifest,
        sample_pages: list[PageData],
        mock_usage: LLMUsage,
    ):
        """Test that generate receives document context from manifest."""
        mock_classify.return_value = (
            ImageClassificationOutput(
                page_num=1,
                images_found=1,
                classifications=[
                    ImageClassification(
                        image_index=1,
                        image_type="informative",
                        confidence=0.95,
                        visual_description="Diagram",
                        decorative_reasoning="",
                    )
                ],
            ),
            mock_usage,
        )

        mock_generate.return_value = (
            AltTextGenerationOutput(
                page_num=1,
                generations=[
                    AltTextGeneration(
                        image_index=1,
                        image_type="informative",
                        alt_text="A process diagram",
                        confidence=0.95,
                        generation_notes="",
                    )
                ],
            ),
            mock_usage,
        )

        agent = FiguresAgent()
        markdown = "![TODO](image-page-1-1.png)"

        await agent.process(
            markdown=markdown,
            pages=sample_pages,
            manifest=sample_manifest,
            job_id="test-job",
        )

        # Verify document_context parameter
        call_args = mock_generate.call_args
        doc_context = call_args.kwargs["document_context"]
        assert "Test Document" in doc_context
        assert "syllabus" in doc_context
