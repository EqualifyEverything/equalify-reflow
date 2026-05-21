"""Unit tests for form reconstruction.

Covers the deterministic ``form`` block renderer (`_render_form_block` /
`_form_dsl_escape`), the `_run_form_reconstructor` subagent wrapper (model
mocked), and that the `has_forms` page flag composes the form procedure into
the page prompt.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.pipeline_viewer import (
    PipelineViewerService,
    _compose_page_prompt,
    _form_dsl_escape,
    _render_form_block,
)
from src.services.pipeline_viewer_models import (
    FormFieldSpec,
    FormFieldType,
    FormReconstructionResult,
    LayoutType,
    PageAttributes,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _render_form_block — DSL rendering
# ---------------------------------------------------------------------------


class TestRenderFormBlock:
    def test_fenced_with_form_language(self):
        block = _render_form_block(FormReconstructionResult(legend="", fields=[]))
        assert block.startswith("```form\n")
        assert block.endswith("\n```")

    def test_legend_emitted_when_present(self):
        result = FormReconstructionResult(legend="Course Registration", fields=[])
        block = _render_form_block(result)
        assert "legend: Course Registration" in block

    def test_legend_omitted_when_empty(self):
        block = _render_form_block(FormReconstructionResult(legend="", fields=[]))
        assert "legend:" not in block

    def test_text_field_line(self):
        result = FormReconstructionResult(fields=[FormFieldSpec(field_type=FormFieldType.TEXT, label="Full name")])
        assert "- text | Full name" in _render_form_block(result)

    def test_required_marker(self):
        result = FormReconstructionResult(
            fields=[FormFieldSpec(field_type=FormFieldType.TEXT, label="Email", required=True)]
        )
        assert "- text* | Email" in _render_form_block(result)

    def test_radio_with_options(self):
        result = FormReconstructionResult(
            fields=[
                FormFieldSpec(
                    field_type=FormFieldType.RADIO,
                    label="Standing",
                    options=["Undergraduate", "Graduate"],
                    required=True,
                )
            ]
        )
        assert "- radio* | Standing | Undergraduate; Graduate" in _render_form_block(result)

    def test_multiselect_and_select_tokens(self):
        result = FormReconstructionResult(
            fields=[
                FormFieldSpec(
                    field_type=FormFieldType.MULTISELECT,
                    label="Topics",
                    options=["AI", "Systems"],
                ),
                FormFieldSpec(
                    field_type=FormFieldType.SELECT,
                    label="State",
                    options=["IL", "IA"],
                ),
            ]
        )
        block = _render_form_block(result)
        assert "- multiselect | Topics | AI; Systems" in block
        assert "- select | State | IL; IA" in block

    def test_checkbox_has_no_options_column(self):
        result = FormReconstructionResult(
            fields=[FormFieldSpec(field_type=FormFieldType.CHECKBOX, label="I agree", required=True)]
        )
        line = [line_ for line_ in _render_form_block(result).splitlines() if "I agree" in line_][0]
        assert line == "- checkbox* | I agree"

    def test_empty_label_falls_back(self):
        result = FormReconstructionResult(fields=[FormFieldSpec(field_type=FormFieldType.TEXT, label="")])
        assert "- text | Field" in _render_form_block(result)


# ---------------------------------------------------------------------------
# _form_dsl_escape — delimiter safety
# ---------------------------------------------------------------------------


class TestFormDslEscape:
    def test_pipe_replaced(self):
        assert _form_dsl_escape("a | b") == "a / b"

    def test_semicolon_replaced(self):
        assert _form_dsl_escape("a; b") == "a, b"

    def test_newlines_collapsed(self):
        assert _form_dsl_escape("line one\nline two") == "line one line two"

    def test_escaping_applied_in_block(self):
        result = FormReconstructionResult(
            fields=[
                FormFieldSpec(
                    field_type=FormFieldType.RADIO,
                    label="Pick | one",
                    options=["a; b", "c"],
                )
            ]
        )
        block = _render_form_block(result)
        assert "Pick / one" in block
        assert "a, b; c" in block
        # the only delimiters left are the structural ones
        field_line = [line for line in block.splitlines() if line.startswith("- ")][0]
        assert field_line.count(" | ") == 2


# ---------------------------------------------------------------------------
# has_forms composes the form procedure into the page prompt
# ---------------------------------------------------------------------------


class TestHasFormsProcedure:
    def test_form_fragment_loaded_when_has_forms(self):
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_forms=True)
        prompt = _compose_page_prompt(attrs)
        assert "reconstruct_form" in prompt

    def test_form_fragment_absent_without_flag(self):
        attrs = PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_forms=False)
        prompt = _compose_page_prompt(attrs)
        assert "reconstruct_form" not in prompt


# ---------------------------------------------------------------------------
# _run_form_reconstructor — subagent wrapper
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    return PipelineViewerService()


def _patched_run(output: FormReconstructionResult):
    """Context managers patching the deferred pydantic_ai imports + model."""
    mock_agent = MagicMock()
    mock_run_result = MagicMock()
    mock_run_result.output = output
    mock_run_result.usage.return_value = MagicMock(request_tokens=12, response_tokens=7)
    mock_agent.run = AsyncMock(return_value=mock_run_result)
    return mock_agent


class TestRunFormReconstructor:
    @pytest.mark.asyncio
    async def test_returns_str_replace_instruction_with_block(self, service):
        output = FormReconstructionResult(
            legend="Consent",
            fields=[FormFieldSpec(field_type=FormFieldType.CHECKBOX, label="I agree", required=True)],
        )
        tokens = []
        with (
            patch("pydantic_ai.Agent", return_value=_patched_run(output)),
            patch("pydantic_ai.models.bedrock.BedrockConverseModel"),
            patch("pydantic_ai.messages.BinaryContent"),
            patch("src.agents.model_factory.get_model_for_tier", return_value=MagicMock()),
        ):
            msg = await service._run_form_reconstructor(
                form_text="☐ I agree",
                surrounding_text="",
                page_image_b64=None,
                page_number=1,
                update_tokens=lambda i, o: tokens.append((i, o)),
            )

        assert "```form" in msg
        assert "legend: Consent" in msg
        assert "- checkbox* | I agree" in msg
        assert "str_replace" in msg
        assert tokens == [(12, 7)]

    @pytest.mark.asyncio
    async def test_empty_fields_reports_not_a_form(self, service):
        output = FormReconstructionResult(fields=[], reasoning="This region is a data table, not a form.")
        with (
            patch("pydantic_ai.Agent", return_value=_patched_run(output)),
            patch("pydantic_ai.models.bedrock.BedrockConverseModel"),
            patch("pydantic_ai.messages.BinaryContent"),
            patch("src.agents.model_factory.get_model_for_tier", return_value=MagicMock()),
        ):
            msg = await service._run_form_reconstructor(
                form_text="| a | b |",
                surrounding_text="",
                page_image_b64=None,
                page_number=2,
                update_tokens=lambda i, o: None,
            )

        assert "```form" not in msg
        assert "No fillable fields" in msg

    @pytest.mark.asyncio
    async def test_subagent_failure_returns_graceful_message(self, service):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM error"))
        with (
            patch("pydantic_ai.Agent", return_value=mock_agent),
            patch("pydantic_ai.models.bedrock.BedrockConverseModel"),
            patch("pydantic_ai.messages.BinaryContent"),
            patch("src.agents.model_factory.get_model_for_tier", return_value=MagicMock()),
        ):
            msg = await service._run_form_reconstructor(
                form_text="Name: ____",
                surrounding_text="",
                page_image_b64=None,
                page_number=3,
                update_tokens=lambda i, o: None,
            )

        assert "Form reconstruction failed" in msg
