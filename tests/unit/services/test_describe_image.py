"""Unit tests for the describe_image tool in page correction agents."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    DocumentChange,
    FigureData,
    ImageDescriptionResult,
    LayoutType,
    OutlineEntry,
    PageAttributes,
    PageCorrectionResult,
    PipelineViewerResult,
    SectionCorrectionResult,
    StructureResult,
)


# Patch targets — these are imported lazily inside methods
_BEDROCK_PATCH = "pydantic_ai.models.bedrock.BedrockConverseModel"
_AGENT_PATCH = "pydantic_ai.Agent"


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def sample_figure():
    return FigureData(
        ref_id="figure-1.png",
        caption="Figure 1: Sample diagram",
        page_number=1,
        image_base64=base64.b64encode(b"fake-image-data").decode(),
    )


@pytest.fixture
def sample_figure_2():
    return FigureData(
        ref_id="figure-2.png",
        caption="Figure 2: Another diagram",
        page_number=2,
        image_base64=base64.b64encode(b"fake-image-data-2").decode(),
    )


@pytest.fixture
def base_result(sample_figure):
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={"v0": "# Intro\n\n![](figures/figure-1.png)\n\nHello world"},
        page_images={"1": "AAAA", "2": "BBBB"},
        page_markdowns={
            "v0": {
                "1": "# Intro\n\n![](figures/figure-1.png)\n\nHello world",
                "2": "# Methods\n\nGoodbye",
            },
        },
        figures=[sample_figure],
        steps=[],
        stats={},
    )


@pytest.fixture
def structure_with_images():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_images=True),
            2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
        },
        outline=[
            OutlineEntry(level=2, text="Intro", page=1),
            OutlineEntry(level=2, text="Methods", page=2),
        ],
        footnotes=[],
    )


@pytest.fixture
def structure_no_images():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_images=False),
            2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
        },
        outline=[
            OutlineEntry(level=2, text="Intro", page=1),
        ],
        footnotes=[],
    )


def _make_mock_agent():
    """Create a mock Agent that tracks tool_plain registrations."""
    mock_agent = MagicMock()
    mock_agent._registered_tools = []

    def tool_plain(func=None, **kwargs):
        if func is not None:
            mock_agent._registered_tools.append(func.__name__)
            return func

        def decorator(f):
            mock_agent._registered_tools.append(f.__name__)
            return f
        return decorator

    mock_agent.tool_plain = tool_plain

    async def mock_run(user_parts, **kwargs):
        result = MagicMock()
        result.output = None
        usage = MagicMock()
        usage.request_tokens = 100
        usage.response_tokens = 50
        result.usage.return_value = usage
        return result

    mock_agent.run = AsyncMock(side_effect=mock_run)
    return mock_agent


class TestDescribeImageToolRegistration:
    """Test that describe_image is conditionally registered in page agent."""

    @pytest.mark.asyncio
    async def test_tool_registered_when_has_images_and_figures(
        self, service, structure_with_images, sample_figure
    ):
        """describe_image should be registered when has_images=True and figures exist."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n![](figures/figure-1.png)\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_images,
                page_figures=[sample_figure],
            )

        assert "describe_image" in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_tool_not_registered_when_no_figures(
        self, service, structure_no_images
    ):
        """describe_image should NOT be registered when no figures."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=2,
                page_markdown="# Methods\n\nGoodbye",
                page_image_b64="BBBB",
                structure=structure_no_images,
            )

        assert "describe_image" not in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_tool_not_registered_when_has_images_but_no_page_figures(
        self, service, structure_with_images
    ):
        """describe_image should NOT be registered when has_images but page_figures is None."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n![](figures/figure-1.png)\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_images,
                page_figures=None,
            )

        assert "describe_image" not in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_describer_token_fields_on_result(
        self, service, structure_with_images, sample_figure
    ):
        """Result should include describer token fields (0 when no actual calls)."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n![](figures/figure-1.png)\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_images,
                page_figures=[sample_figure],
            )

        assert result.describer_input_tokens == 0
        assert result.describer_output_tokens == 0


class TestImageDescriberSubagent:
    """Test the _run_image_describer method."""

    @pytest.mark.asyncio
    async def test_successful_description(self, service, sample_figure):
        """Should return formatted alt text instruction."""
        mock_result = ImageDescriptionResult(
            image_category="informative",
            alt_text="Flowchart showing data processing steps",
            is_decorative=False,
            confidence="high",
            reasoning="Clear diagram with labeled boxes",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 500
        mock_usage.response_tokens = 100
        mock_agent_result.usage.return_value = mock_usage

        token_updates = []

        def track_tokens(inp, out):
            token_updates.append((inp, out))

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_image_describer(
                figure=sample_figure,
                current_markdown="# Intro\n\n![](figures/figure-1.png)\n\nHello",
                page_images={1: "AAAA"},
                describer_tokens={"input": 0, "output": 0},
                update_tokens=track_tokens,
            )

        assert "Flowchart showing data processing steps" in result
        assert "figure-1.png" in result
        assert token_updates == [(500, 100)]

    @pytest.mark.asyncio
    async def test_decorative_image(self, service, sample_figure):
        """Decorative images should return instruction for empty alt."""
        mock_result = ImageDescriptionResult(
            image_category="decorative",
            alt_text="",
            is_decorative=True,
            confidence="high",
            reasoning="Decorative border",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 300
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_image_describer(
                figure=sample_figure,
                current_markdown="![](figures/figure-1.png)",
                page_images={1: "AAAA"},
                describer_tokens={"input": 0, "output": 0},
                update_tokens=lambda i, o: None,
            )

        assert "decorative" in result.lower()
        assert "empty" in result.lower()

    @pytest.mark.asyncio
    async def test_describer_failure_returns_error(self, service, sample_figure):
        """If the describer agent raises, should return error string."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Model timeout"))

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_image_describer(
                figure=sample_figure,
                current_markdown="![](figures/figure-1.png)",
                page_images={1: "AAAA"},
                describer_tokens={"input": 0, "output": 0},
                update_tokens=lambda i, o: None,
            )

        assert "ERROR" in result
        assert "figure-1.png" in result

    @pytest.mark.asyncio
    async def test_surrounding_text_extracted(self, service, sample_figure):
        """The describer should receive surrounding markdown context."""
        captured_user_parts = []

        mock_result = ImageDescriptionResult(
            image_category="informative",
            alt_text="Test alt",
            is_decorative=False,
            confidence="high",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_image_describer(
                figure=sample_figure,
                current_markdown="Some text before\n\n![](figures/figure-1.png)\n\nSome text after",
                page_images={1: "AAAA"},
                describer_tokens={"input": 0, "output": 0},
                update_tokens=lambda i, o: None,
            )

        # The last user part should be the text message with surrounding context
        text_parts = [p for p in captured_user_parts if isinstance(p, str)]
        assert any("figure-1.png" in p for p in text_parts)


class TestDescribeImageTokenTracking:
    """Test describer token aggregation in _step_page_content."""

    @pytest.mark.asyncio
    async def test_describer_tokens_aggregated(
        self, service, base_result, structure_with_images
    ):
        """Describer tokens should be included in step totals."""

        async def mock_run_page_agent(page_num, **kwargs):
            describer_in = 500 if page_num == 1 else 0
            describer_out = 100 if page_num == 1 else 0
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=base_result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
                input_tokens=200,
                output_tokens=50,
                describer_input_tokens=describer_in,
                describer_output_tokens=describer_out,
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(base_result, structure_with_images)

        step = base_result.steps[-1]
        # Page agent tokens: 200*2=400 input, 50*2=100 output
        # Describer tokens: 500 input, 100 output (only page 1)
        # Combined: 900 input, 200 output
        assert step.input_tokens == 900
        assert step.output_tokens == 200
        assert step.metadata["describer_input_tokens"] == 500
        assert step.metadata["describer_output_tokens"] == 100

    @pytest.mark.asyncio
    async def test_describer_tokens_zero_when_no_images(
        self, service, structure_no_images
    ):
        """Describer tokens should be 0 when no images in document."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# Intro\n\nHello world"},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": "# Intro\n\nHello world"}},
            figures=[],
            steps=[],
            stats={},
        )

        async def mock_run_page_agent(page_num, **kwargs):
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
                input_tokens=200,
                output_tokens=50,
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure_no_images)

        step = result.steps[-1]
        assert step.metadata["describer_input_tokens"] == 0
        assert step.metadata["describer_output_tokens"] == 0


class TestDescribeImageModelDefaults:
    """Test model defaults for describer token fields."""

    def test_section_correction_result_defaults(self):
        """SectionCorrectionResult should default describer tokens to 0."""
        result = SectionCorrectionResult(
            section_index=0,
            heading_text="Test",
            corrected_markdown="test",
            pages=[1],
        )
        assert result.describer_input_tokens == 0
        assert result.describer_output_tokens == 0

    def test_page_correction_result_defaults(self):
        """PageCorrectionResult should default describer tokens to 0."""
        result = PageCorrectionResult(
            page=1,
            corrected_markdown="test",
        )
        assert result.describer_input_tokens == 0
        assert result.describer_output_tokens == 0

    def test_image_description_result_defaults(self):
        """ImageDescriptionResult should have sensible defaults."""
        result = ImageDescriptionResult(
            image_category="informative", alt_text="A chart"
        )
        assert result.is_decorative is False
        assert result.confidence == "high"
        assert result.reasoning == ""
        assert result.image_category == "informative"


class TestPageFigureFiltering:
    """Test that figures are correctly filtered per page."""

    @pytest.mark.asyncio
    async def test_figures_filtered_by_page(
        self, service, sample_figure, sample_figure_2
    ):
        """Each page should only get figures from that page."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "# Intro\n\n![](figures/figure-1.png)\n\n# Methods\n\n![](figures/figure-2.png)"},
            page_images={"1": "AAAA", "2": "BBBB"},
            page_markdowns={"v0": {"1": "page1", "2": "page2"}},
            figures=[sample_figure, sample_figure_2],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_images=True),
                2: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_images=True),
            },
            outline=[
                OutlineEntry(level=2, text="Intro", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
            ],
            footnotes=[],
        )

        captured_figures: dict[int, list[str] | None] = {}

        async def mock_run_page_agent(page_num, **kwargs):
            pf = kwargs.get("page_figures")
            if pf:
                captured_figures[page_num] = [f.ref_id for f in pf]
            else:
                captured_figures[page_num] = None
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        assert captured_figures[1] == ["figure-1.png"]
        assert captured_figures[2] == ["figure-2.png"]

    @pytest.mark.asyncio
    async def test_no_figures_passes_none(self, service):
        """Pages with no figures should get None."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=1,
            versions={"v0": "# Intro\n\nHello"},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": "# Intro\n\nHello"}},
            figures=[],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
            },
            outline=[OutlineEntry(level=2, text="Intro", page=1)],
            footnotes=[],
        )

        captured_figures = {}

        async def mock_run_page_agent(page_num, **kwargs):
            captured_figures[page_num] = kwargs.get("page_figures")
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        assert captured_figures[1] is None


class TestImageIsolation:
    """Test that the describer only receives the cropped figure, not the full page."""

    @pytest.mark.asyncio
    async def test_only_cropped_figure_sent_when_available(
        self, service, sample_figure
    ):
        """When image_base64 is present, only the cropped figure is sent (no page image)."""
        from pydantic_ai.messages import BinaryContent

        captured_user_parts = []

        mock_result = ImageDescriptionResult(
            image_category="informative",
            alt_text="Test",
            is_decorative=False,
            confidence="high",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_image_describer(
                figure=sample_figure,
                current_markdown="![](figures/figure-1.png)",
                page_images={1: "PAGE_IMAGE_B64"},
                describer_tokens={"input": 0, "output": 0},
                update_tokens=lambda i, o: None,
            )

        # Should have exactly 1 BinaryContent (the cropped figure) + 1 text message
        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 1

        # Text should NOT contain fallback mode guidance
        text_parts = [p for p in captured_user_parts if isinstance(p, str)]
        assert not any("full page image" in p for p in text_parts)

    @pytest.mark.asyncio
    async def test_page_image_fallback_when_no_crop(self, service):
        """When image_base64 is empty, falls back to full page image with guidance."""
        from pydantic_ai.messages import BinaryContent

        figure_no_crop = FigureData(
            ref_id="figure-3.png",
            caption="Figure 3: No crop available",
            page_number=1,
            image_base64="",  # Empty — Docling failed to extract
        )

        captured_user_parts = []

        mock_result = ImageDescriptionResult(
            image_category="informative",
            alt_text="Fallback test",
            is_decorative=False,
            confidence="medium",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 100
        mock_usage.response_tokens = 50
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        page_b64 = base64.b64encode(b"page-image-data").decode()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_image_describer(
                figure=figure_no_crop,
                current_markdown="![](figures/figure-3.png)",
                page_images={1: page_b64},
                describer_tokens={"input": 0, "output": 0},
                update_tokens=lambda i, o: None,
            )

        # Should have 1 BinaryContent (the page image fallback) + 1 text message
        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 1

        # Text SHOULD contain fallback mode guidance
        text_parts = [p for p in captured_user_parts if isinstance(p, str)]
        assert any("full page image" in p for p in text_parts)
        assert any("figure-3.png" in p for p in text_parts)

    @pytest.mark.asyncio
    async def test_no_images_sent_when_neither_available(self, service):
        """When both crop and page image are missing, only text is sent."""
        from pydantic_ai.messages import BinaryContent

        figure_no_crop = FigureData(
            ref_id="figure-4.png",
            caption="Figure 4: Ghost figure",
            page_number=5,
            image_base64="",
        )

        captured_user_parts = []

        mock_result = ImageDescriptionResult(
            image_category="informative",
            alt_text="No image available",
            is_decorative=False,
            confidence="low",
        )
        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 50
        mock_usage.response_tokens = 30
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()

        async def capture_run(user_parts, **kwargs):
            captured_user_parts.extend(user_parts)
            return mock_agent_result

        mock_agent.run = AsyncMock(side_effect=capture_run)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_image_describer(
                figure=figure_no_crop,
                current_markdown="![](figures/figure-4.png)",
                page_images={},  # No page images at all
                describer_tokens={"input": 0, "output": 0},
                update_tokens=lambda i, o: None,
            )

        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 0


class TestImageClassification:
    """Test the image_category field on ImageDescriptionResult."""

    def test_decorative_category(self):
        result = ImageDescriptionResult(
            image_category="decorative",
            alt_text="",
            is_decorative=True,
            confidence="high",
            reasoning="University logo in page header",
        )
        assert result.image_category == "decorative"
        assert result.is_decorative is True
        assert result.alt_text == ""

    def test_informative_category(self):
        result = ImageDescriptionResult(
            image_category="informative",
            alt_text="Eastern box turtle on forest floor",
            is_decorative=False,
            confidence="high",
        )
        assert result.image_category == "informative"
        assert result.is_decorative is False

    def test_complex_informative_category(self):
        result = ImageDescriptionResult(
            image_category="complex_informative",
            alt_text="Bar chart showing enrollment growth from 450 to 680 students",
            is_decorative=False,
            confidence="high",
            reasoning="Data visualization with multiple data points — long description recommended",
        )
        assert result.image_category == "complex_informative"

    def test_invalid_category_rejected(self):
        """Invalid image_category values should be rejected by Pydantic."""
        with pytest.raises(Exception):
            ImageDescriptionResult(
                image_category="unknown",
                alt_text="Test",
            )


class TestDescriberUserMessage:
    """Test the build_describer_user_message helper."""

    def test_standard_mode(self):
        from src.agents.prompts.image_description import (
            build_describer_user_message,
        )

        msg = build_describer_user_message(
            caption="Figure 1: Test",
            surrounding_text="some context",
            ref_id="figure-1.png",
        )
        assert "figure-1.png" in msg
        assert "Figure 1: Test" in msg
        assert "full page image" not in msg
        assert "classify" in msg.lower()

    def test_fallback_mode(self):
        from src.agents.prompts.image_description import (
            build_describer_user_message,
        )

        msg = build_describer_user_message(
            caption="Figure 2: Chart",
            surrounding_text="nearby text",
            ref_id="figure-2.png",
            fallback_mode=True,
        )
        assert "full page image" in msg
        assert "figure-2.png" in msg
        assert "ONLY that figure" in msg

    def test_no_caption_or_context(self):
        from src.agents.prompts.image_description import (
            build_describer_user_message,
        )

        msg = build_describer_user_message(
            caption="",
            surrounding_text="",
            ref_id="figure-5.png",
        )
        assert "figure-5.png" in msg
        assert "Caption" not in msg
        assert "Surrounding text" not in msg
