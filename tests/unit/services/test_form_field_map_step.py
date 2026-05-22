"""Unit tests for the Form Field Mapper pipeline step.

``_step_form_field_map`` uses deferred imports, so we patch at the source
modules (``pydantic_ai.*``, ``src.agents.prompts.form_field_mapper.*``) rather
than the consumer module. Mirrors the mocking style in
``test_pipeline_viewer_phases.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.prompts.form_field_mapper import FormFieldEntry, FormFieldMapOutput
from src.services.pipeline_viewer import PipelineViewerService
from src.services.pipeline_viewer_models import PipelineViewerResult, StepResult


@pytest.fixture
def service():
    return PipelineViewerService()


def _result_with_markdown(versions: dict[str, str]) -> PipelineViewerResult:
    return PipelineViewerResult(
        filename="form.pdf",
        total_pages=1,
        versions=versions,
        page_images={},
        page_markdowns={},
        figures=[],
        steps=[
            StepResult(
                name="docling",
                display_name="Docling Extraction",
                version_before=None,
                version_after="v0",
                elapsed_ms=10,
                changes=[],
                metadata={},
            ),
        ],
        stats={"total_chars": 120},
    )


def _mock_run_result(output: FormFieldMapOutput, request_tokens: int = 100, response_tokens: int = 20):
    mock_run_result = MagicMock()
    mock_run_result.output = output
    mock_run_result.usage = MagicMock(
        return_value=MagicMock(request_tokens=request_tokens, response_tokens=response_tokens)
    )
    return mock_run_result


class TestFormFieldMapStep:
    @pytest.mark.asyncio
    async def test_happy_path_records_field_dictionary(self, service):
        """A successful run appends a non-skipped StepResult with field metadata."""
        result = _result_with_markdown({"v0": "# Application\n\nName: ____\nDate of birth: ____"})

        output = FormFieldMapOutput(
            document_kind="Generic Application Form",
            fields=[
                FormFieldEntry(
                    label="Name",
                    field_type="text",
                    description="The applicant's full legal name.",
                    options=[],
                    required=True,
                    section="",
                    suggested_key="applicant_name",
                ),
                FormFieldEntry(
                    label="Date of birth",
                    field_type="date",
                    description="The applicant's date of birth.",
                    options=[],
                    required=False,
                    section="",
                    suggested_key="date_of_birth",
                ),
            ],
            overall_confidence="high",
            notes="",
        )

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=_mock_run_result(output))

        with patch("pydantic_ai.Agent", return_value=mock_agent), patch(
            "pydantic_ai.models.bedrock.BedrockConverseModel"
        ), patch(
            "src.agents.prompts.form_field_mapper.FORM_FIELD_MAPPER_SYSTEM_PROMPT", "test"
        ):
            await service._step_form_field_map(result)

        step = next(s for s in result.steps if s.name == "form_field_map")
        assert step.skipped is False
        assert step.error is None
        assert step.version_before == "v0"
        assert step.version_after == "v0"  # analysis-only, no markdown change
        assert step.metadata["field_count"] == 2
        assert step.metadata["document_kind"] == "Generic Application Form"
        assert step.metadata["overall_confidence"] == "high"
        keys = [f["suggested_key"] for f in step.metadata["fields"]]
        assert keys == ["applicant_name", "date_of_birth"]
        assert step.input_tokens == 100
        assert step.output_tokens == 20

    @pytest.mark.asyncio
    async def test_skips_when_no_markdown(self, service):
        """With no markdown to map, the step is recorded as skipped (no agent call)."""
        result = _result_with_markdown({})  # no versions

        with patch("pydantic_ai.Agent") as agent_ctor:
            await service._step_form_field_map(result)
            agent_ctor.assert_not_called()

        step = next(s for s in result.steps if s.name == "form_field_map")
        assert step.skipped is True
        assert step.metadata.get("reason") == "no_markdown_to_map"

    @pytest.mark.asyncio
    async def test_records_error_without_raising(self, service):
        """Agent failure is captured on the StepResult, not propagated."""
        result = _result_with_markdown({"v0": "# Form\n\nField: ____"})

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM error"))

        with patch("pydantic_ai.Agent", return_value=mock_agent), patch(
            "pydantic_ai.models.bedrock.BedrockConverseModel"
        ), patch(
            "src.agents.prompts.form_field_mapper.FORM_FIELD_MAPPER_SYSTEM_PROMPT", "test"
        ):
            await service._step_form_field_map(result)

        step = next(s for s in result.steps if s.name == "form_field_map")
        assert step.skipped is False
        assert step.error == "LLM error"
        assert step.metadata == {}
