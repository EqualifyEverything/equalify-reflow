"""Unit tests for the reconstruct_list tool in page correction agents."""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import (
    FigureData,
    LayoutType,
    ListReconstructionResult,
    OutlineEntry,
    PageAttributes,
    PageCorrectionResult,
    PipelineViewerResult,
    StructurePageOutput,
    StructureResult,
)


# Patch targets — these are imported lazily inside methods
_BEDROCK_PATCH = "pydantic_ai.models.bedrock.BedrockConverseModel"
_AGENT_PATCH = "pydantic_ai.Agent"


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def base_result():
    return PipelineViewerResult(
        filename="test.pdf",
        total_pages=2,
        versions={"v0": "# Intro\n\n- Item 1\n- Item 2\n\nHello world"},
        page_images={"1": "AAAA", "2": "BBBB"},
        page_markdowns={
            "v0": {
                "1": "# Intro\n\n- Item 1\n- Item 2\n\nHello world",
                "2": "# Methods\n\nGoodbye",
            },
        },
        figures=[],
        steps=[],
        stats={},
    )


@pytest.fixture
def structure_with_lists():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=True),
            2: PageAttributes(layout=LayoutType.SINGLE_COLUMN),
        },
        outline=[
            OutlineEntry(level=2, text="Intro", page=1),
            OutlineEntry(level=2, text="Methods", page=2),
        ],
        footnotes=[],
    )


@pytest.fixture
def structure_no_lists():
    return StructureResult(
        page_attributes={
            1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=False),
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


# -----------------------------------------------------------------------
# Test has_lists in PageAttributes
# -----------------------------------------------------------------------


class TestHasListsPageAttribute:
    """Test has_lists field on PageAttributes model."""

    def test_default_is_false(self):
        """has_lists should default to False."""
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN)
        assert attrs.has_lists is False

    def test_can_be_set_true(self):
        """has_lists can be explicitly set to True."""
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=True)
        assert attrs.has_lists is True

    def test_accepted_by_structure_page_output(self):
        """StructurePageOutput should accept page_attributes with has_lists."""
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=True)
        output = StructurePageOutput(page_attributes=attrs)
        assert output.page_attributes.has_lists is True


# -----------------------------------------------------------------------
# Test _compose_page_prompt integration
# -----------------------------------------------------------------------


class TestComposePagePromptWithLists:
    """Test that has_lists triggers loading the list fragment."""

    def test_has_lists_true_includes_fragment(self):
        """When has_lists=True, prompt should include list guidance."""
        from src.services.pipeline_viewer import _compose_page_prompt

        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=True)
        prompt = _compose_page_prompt(attrs)
        assert "Pages with Lists" in prompt
        assert "reconstruct_list" in prompt

    def test_has_lists_false_excludes_fragment(self):
        """When has_lists=False, prompt should NOT include list guidance."""
        from src.services.pipeline_viewer import _compose_page_prompt

        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=False)
        prompt = _compose_page_prompt(attrs)
        assert "Pages with Lists" not in prompt
        assert "reconstruct_list" not in prompt


# -----------------------------------------------------------------------
# Test tool registration
# -----------------------------------------------------------------------


class TestReconstructListToolRegistration:
    """Test that reconstruct_list is conditionally registered in page agent."""

    @pytest.mark.asyncio
    async def test_tool_registered_when_has_lists(
        self, service, structure_with_lists
    ):
        """reconstruct_list should be registered when has_lists=True."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n- Item 1\n- Item 2\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_lists,
            )

        assert "reconstruct_list" in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_tool_not_registered_when_no_lists(
        self, service, structure_no_lists
    ):
        """reconstruct_list should NOT be registered when has_lists=False."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\nHello world",
                page_image_b64="AAAA",
                structure=structure_no_lists,
            )

        assert "reconstruct_list" not in mock_agent._registered_tools

    @pytest.mark.asyncio
    async def test_list_reconstructor_token_fields_on_result(
        self, service, structure_with_lists
    ):
        """Result should include list reconstructor token fields (0 when no actual calls)."""
        mock_agent = _make_mock_agent()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n- Item 1\n- Item 2\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_lists,
            )

        assert result.list_reconstructor_input_tokens == 0
        assert result.list_reconstructor_output_tokens == 0


# -----------------------------------------------------------------------
# Test tool error handling
# -----------------------------------------------------------------------


class TestReconstructListToolErrors:
    """Test error handling in the reconstruct_list tool."""

    @pytest.mark.asyncio
    async def test_list_text_not_found_returns_error(
        self, service, structure_with_lists
    ):
        """When list_text doesn't exist in markdown, tool returns error."""
        mock_agent = _make_mock_agent()
        tool_func = None

        original_tool_plain = mock_agent.tool_plain

        def capture_tool_plain(func=None, **kwargs):
            nonlocal tool_func
            if func is not None and func.__name__ == "reconstruct_list":
                tool_func = func
            return original_tool_plain(func, **kwargs)

        mock_agent.tool_plain = capture_tool_plain

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n- Item 1\n- Item 2\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_lists,
            )

        assert tool_func is not None
        result = await tool_func(
            list_text="- Nonexistent item\n- Not here",
            reasoning="testing error",
        )
        assert "ERROR" in result
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_max_calls_exceeded_returns_error(
        self, service, structure_with_lists
    ):
        """When max calls reached, tool returns error."""
        mock_agent = _make_mock_agent()
        tool_func = None

        original_tool_plain = mock_agent.tool_plain

        def capture_tool_plain(func=None, **kwargs):
            nonlocal tool_func
            if func is not None and func.__name__ == "reconstruct_list":
                tool_func = func
            return original_tool_plain(func, **kwargs)

        mock_agent.tool_plain = capture_tool_plain

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_page_agent(
                page_num=1,
                page_markdown="# Intro\n\n- Item 1\n- Item 2\n\nHello",
                page_image_b64="AAAA",
                structure=structure_with_lists,
            )

        assert tool_func is not None

        # Mock _run_list_reconstructor to avoid actual agent call
        mock_reconstructor_result = "Reconstructed as unordered markdown list.\nUse str_replace..."
        service._run_list_reconstructor = AsyncMock(return_value=mock_reconstructor_result)

        # Call 5 times (max)
        for _ in range(5):
            await tool_func(
                list_text="- Item 1\n- Item 2",
                reasoning="testing max calls",
            )

        # 6th call should be rejected
        result = await tool_func(
            list_text="- Item 1\n- Item 2",
            reasoning="one more",
        )
        assert "ERROR" in result
        assert "Maximum" in result


# -----------------------------------------------------------------------
# Test subagent call
# -----------------------------------------------------------------------


class TestListReconstructorSubagent:
    """Test the _run_list_reconstructor method."""

    @pytest.mark.asyncio
    async def test_successful_reconstruction_unordered(self, service):
        """Should return formatted instruction for unordered list."""
        mock_result = ListReconstructionResult(
            list_type="unordered",
            reconstructed_content="- Item A\n- Item B\n  - Sub-item B1",
            confidence="high",
            reasoning="Items clearly separated in image",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 400
        mock_usage.response_tokens = 80
        mock_agent_result.usage.return_value = mock_usage

        token_updates = []

        def track_tokens(inp, out):
            token_updates.append((inp, out))

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_list_reconstructor(
                list_text="- Item A\n- Item B Sub-item B1",
                surrounding_text="Some context around the list",
                page_image_b64="AAAA",
                page_number=1,
                update_tokens=track_tokens,
            )

        assert "unordered markdown list" in result
        assert "- Item A" in result
        assert token_updates == [(400, 80)]

    @pytest.mark.asyncio
    async def test_successful_reconstruction_ordered(self, service):
        """Should return formatted instruction for ordered list."""
        mock_result = ListReconstructionResult(
            list_type="ordered",
            reconstructed_content="1. First\n2. Second\n3. Third",
            confidence="high",
            reasoning="Numbers clearly visible",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 350
        mock_usage.response_tokens = 70
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_list_reconstructor(
                list_text="1. First 2. Second 3. Third",
                surrounding_text="context",
                page_image_b64="BBBB",
                page_number=2,
                update_tokens=lambda i, o: None,
            )

        assert "ordered markdown list" in result
        assert "1. First" in result

    @pytest.mark.asyncio
    async def test_successful_reconstruction_definition(self, service):
        """Should return HTML definition list instruction."""
        mock_result = ListReconstructionResult(
            list_type="definition",
            reconstructed_content="<dl>\n  <dt>Term 1</dt>\n  <dd>Definition 1.</dd>\n</dl>",
            confidence="high",
            reasoning="Term/definition pairs identified",
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = mock_result
        mock_usage = MagicMock()
        mock_usage.request_tokens = 300
        mock_usage.response_tokens = 60
        mock_agent_result.usage.return_value = mock_usage

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=mock_agent_result)

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_list_reconstructor(
                list_text="**Term 1** Definition 1.",
                surrounding_text="glossary section",
                page_image_b64="CCCC",
                page_number=3,
                update_tokens=lambda i, o: None,
            )

        assert "HTML definition list" in result
        assert "<dl>" in result

    @pytest.mark.asyncio
    async def test_failure_returns_error(self, service):
        """If the reconstructor agent raises, should return error string."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Model timeout"))

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            result = await service._run_list_reconstructor(
                list_text="- Item 1\n- Item 2",
                surrounding_text="context",
                page_image_b64="AAAA",
                page_number=1,
                update_tokens=lambda i, o: None,
            )

        assert "failed" in result.lower()
        assert "str_replace" in result

    @pytest.mark.asyncio
    async def test_page_image_sent_as_binary_content(self, service):
        """The reconstructor should receive the page image as BinaryContent."""
        from pydantic_ai.messages import BinaryContent

        captured_user_parts = []

        mock_result = ListReconstructionResult(
            list_type="unordered",
            reconstructed_content="- Item 1\n- Item 2",
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

        page_b64 = base64.b64encode(b"page-image-data").decode()

        with patch(_BEDROCK_PATCH), patch(_AGENT_PATCH, return_value=mock_agent):
            await service._run_list_reconstructor(
                list_text="- Item 1\n- Item 2",
                surrounding_text="context",
                page_image_b64=page_b64,
                page_number=1,
                update_tokens=lambda i, o: None,
            )

        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 1

    @pytest.mark.asyncio
    async def test_no_image_when_page_image_none(self, service):
        """When page_image_b64 is None, no BinaryContent should be sent."""
        from pydantic_ai.messages import BinaryContent

        captured_user_parts = []

        mock_result = ListReconstructionResult(
            list_type="unordered",
            reconstructed_content="- Item 1",
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
            await service._run_list_reconstructor(
                list_text="- Item 1",
                surrounding_text="context",
                page_image_b64=None,
                page_number=1,
                update_tokens=lambda i, o: None,
            )

        binary_parts = [
            p for p in captured_user_parts if isinstance(p, BinaryContent)
        ]
        assert len(binary_parts) == 0

    @pytest.mark.asyncio
    async def test_user_message_contains_list_text(self, service):
        """The user message should contain the list text."""
        captured_user_parts = []

        mock_result = ListReconstructionResult(
            list_type="unordered",
            reconstructed_content="- Item 1\n- Item 2",
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
            await service._run_list_reconstructor(
                list_text="- Item 1\n- Item 2",
                surrounding_text="near a heading",
                page_image_b64=None,
                page_number=5,
                update_tokens=lambda i, o: None,
            )

        text_parts = [p for p in captured_user_parts if isinstance(p, str)]
        assert any("- Item 1" in p for p in text_parts)
        assert any("page 5" in p for p in text_parts)


# -----------------------------------------------------------------------
# Test token accumulation
# -----------------------------------------------------------------------


class TestListReconstructorTokenTracking:
    """Test list reconstructor token aggregation in _step_page_content."""

    @pytest.mark.asyncio
    async def test_list_tokens_aggregated(
        self, service, base_result, structure_with_lists
    ):
        """List reconstructor tokens should be included in step totals."""

        async def mock_run_page_agent(page_num, **kwargs):
            list_in = 300 if page_num == 1 else 0
            list_out = 60 if page_num == 1 else 0
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=base_result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
                input_tokens=200,
                output_tokens=50,
                list_reconstructor_input_tokens=list_in,
                list_reconstructor_output_tokens=list_out,
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(base_result, structure_with_lists)

        step = base_result.steps[-1]
        # Page agent tokens: 200*2=400 input, 50*2=100 output
        # List reconstructor tokens: 300 input, 60 output (only page 1)
        # Combined: 700 input, 160 output
        assert step.input_tokens == 700
        assert step.output_tokens == 160
        assert step.metadata["list_reconstructor_input_tokens"] == 300
        assert step.metadata["list_reconstructor_output_tokens"] == 60

    @pytest.mark.asyncio
    async def test_list_tokens_zero_when_no_lists(
        self, service, structure_no_lists
    ):
        """List reconstructor tokens should be 0 when no lists in document."""
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

        await service._step_page_content(result, structure_no_lists)

        step = result.steps[-1]
        assert step.metadata["list_reconstructor_input_tokens"] == 0
        assert step.metadata["list_reconstructor_output_tokens"] == 0

    @pytest.mark.asyncio
    async def test_multiple_pages_tokens_sum(self, service):
        """Tokens from multiple pages with lists should sum correctly."""
        result = PipelineViewerResult(
            filename="test.pdf",
            total_pages=2,
            versions={"v0": "page1\n\npage2"},
            page_images={"1": "AAAA", "2": "BBBB"},
            page_markdowns={"v0": {"1": "page1", "2": "page2"}},
            figures=[],
            steps=[],
            stats={},
        )

        structure = StructureResult(
            page_attributes={
                1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=True),
                2: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_lists=True),
            },
            outline=[
                OutlineEntry(level=2, text="Intro", page=1),
                OutlineEntry(level=2, text="Methods", page=2),
            ],
            footnotes=[],
        )

        async def mock_run_page_agent(page_num, **kwargs):
            return PageCorrectionResult(
                page=page_num,
                corrected_markdown=result.page_markdowns["v0"][str(page_num)],
                changes=[],
                issues=[],
                input_tokens=100,
                output_tokens=25,
                list_reconstructor_input_tokens=200,
                list_reconstructor_output_tokens=40,
            )

        service._run_page_agent = mock_run_page_agent

        await service._step_page_content(result, structure)

        step = result.steps[-1]
        # 2 pages * 200 = 400 list input, 2 * 40 = 80 list output
        assert step.metadata["list_reconstructor_input_tokens"] == 400
        assert step.metadata["list_reconstructor_output_tokens"] == 80


# -----------------------------------------------------------------------
# Test model defaults
# -----------------------------------------------------------------------


class TestListReconstructionModelDefaults:
    """Test model defaults for list reconstruction result."""

    def test_page_correction_result_defaults(self):
        """PageCorrectionResult should default list reconstructor tokens to 0."""
        result = PageCorrectionResult(
            page=1,
            corrected_markdown="test",
        )
        assert result.list_reconstructor_input_tokens == 0
        assert result.list_reconstructor_output_tokens == 0

    def test_list_reconstruction_result_defaults(self):
        """ListReconstructionResult should have sensible defaults."""
        result = ListReconstructionResult(
            list_type="unordered",
            reconstructed_content="- Item 1",
        )
        assert result.confidence == "high"
        assert result.reasoning == ""
        assert result.list_type == "unordered"

    def test_list_reconstruction_result_ordered(self):
        """ListReconstructionResult accepts ordered type."""
        result = ListReconstructionResult(
            list_type="ordered",
            reconstructed_content="1. Item 1",
        )
        assert result.list_type == "ordered"

    def test_list_reconstruction_result_definition(self):
        """ListReconstructionResult accepts definition type."""
        result = ListReconstructionResult(
            list_type="definition",
            reconstructed_content="<dl><dt>Term</dt><dd>Def</dd></dl>",
        )
        assert result.list_type == "definition"

    def test_invalid_list_type_rejected(self):
        """Invalid list_type values should be rejected by Pydantic."""
        with pytest.raises(Exception):
            ListReconstructionResult(
                list_type="invalid",
                reconstructed_content="test",
            )


# -----------------------------------------------------------------------
# Test prompt builder
# -----------------------------------------------------------------------


class TestListReconstructionPrompt:
    """Test the build_list_user_message helper and system prompt."""

    def test_build_user_message_basic(self):
        from src.agents.prompts.list_reconstruction import (
            build_list_user_message,
        )

        msg = build_list_user_message(
            list_text="- Item 1\n- Item 2",
            surrounding_text="some context around",
            page_number=3,
        )
        assert "page 3" in msg
        assert "- Item 1" in msg
        assert "some context around" in msg
        assert "Classify" in msg

    def test_build_user_message_no_surrounding_text(self):
        from src.agents.prompts.list_reconstruction import (
            build_list_user_message,
        )

        msg = build_list_user_message(
            list_text="- A\n- B",
            surrounding_text="",
            page_number=1,
        )
        assert "- A" in msg
        assert "Surrounding text" not in msg

    def test_build_user_message_with_surrounding_text(self):
        from src.agents.prompts.list_reconstruction import (
            build_list_user_message,
        )

        msg = build_list_user_message(
            list_text="1. First\n2. Second",
            surrounding_text="## Heading\n\nBefore the list",
            page_number=7,
        )
        assert "page 7" in msg
        assert "Surrounding text" in msg
        assert "## Heading" in msg

    def test_system_prompt_is_nonempty(self):
        from src.agents.prompts.list_reconstruction import (
            LIST_RECONSTRUCTOR_SYSTEM_PROMPT,
        )

        assert isinstance(LIST_RECONSTRUCTOR_SYSTEM_PROMPT, str)
        assert len(LIST_RECONSTRUCTOR_SYSTEM_PROMPT) > 100
        assert "list reconstruction" in LIST_RECONSTRUCTOR_SYSTEM_PROMPT.lower()

    def test_system_prompt_covers_all_list_types(self):
        from src.agents.prompts.list_reconstruction import (
            LIST_RECONSTRUCTOR_SYSTEM_PROMPT,
        )

        assert "Unordered list" in LIST_RECONSTRUCTOR_SYSTEM_PROMPT
        assert "Ordered list" in LIST_RECONSTRUCTOR_SYSTEM_PROMPT
        assert "Definition list" in LIST_RECONSTRUCTOR_SYSTEM_PROMPT
        assert "<dl>" in LIST_RECONSTRUCTOR_SYSTEM_PROMPT
