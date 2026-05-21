"""Unit tests for the Form Fields pipeline step.

Covers the pure accessible-HTML renderer (`_render_form_field_html`), the
deterministic anchor-replacement injector (`_apply_form_field`), and the
`_step_form_fields` orchestration (with the vision agent mocked).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.pipeline_viewer import (
    PipelineViewerService,
    _apply_form_field,
    _render_form_field_html,
)
from src.services.pipeline_viewer_models import (
    FormFieldInfo,
    FormFieldOption,
    FormFieldsPageOutput,
    FormFieldType,
    PipelineViewerResult,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def base_result():
    """A 1-page result whose newest per-page version is v0."""
    return PipelineViewerResult(
        filename="form.pdf",
        total_pages=1,
        versions={"v0": "# Application\n\nName: ____________"},
        page_images={"1": "AAAA"},
        page_markdowns={"v0": {"1": "# Application\n\nName: ____________"}},
        figures=[],
        steps=[],
        stats={},
    )


# ---------------------------------------------------------------------------
# _render_form_field_html — accessible markup per field type
# ---------------------------------------------------------------------------


class TestRenderFormFieldHtml:
    def test_text_field_label_wired_to_input(self):
        field = FormFieldInfo(field_type=FormFieldType.TEXT, label="Full name", anchor_text="Name:")
        html = _render_form_field_html(field, "ff-p1-0")
        assert '<label for="ff-p1-0">Full name</label>' in html
        assert '<input type="text" id="ff-p1-0"' in html
        assert "readonly" in html

    def test_required_field_gets_aria_required(self):
        field = FormFieldInfo(
            field_type=FormFieldType.TEXT,
            label="Email",
            anchor_text="Email",
            required=True,
        )
        html = _render_form_field_html(field, "ff-p1-1")
        assert 'aria-required="true"' in html
        assert '<abbr title="required">*</abbr>' in html

    def test_date_field_uses_date_input(self):
        field = FormFieldInfo(field_type=FormFieldType.DATE, label="DOB", anchor_text="Date of birth")
        html = _render_form_field_html(field, "ff-p1-0")
        assert '<input type="date"' in html

    def test_textarea_field(self):
        field = FormFieldInfo(field_type=FormFieldType.TEXTAREA, label="Comments", anchor_text="Comments")
        html = _render_form_field_html(field, "ff-p1-0")
        assert '<textarea id="ff-p1-0"' in html
        assert "</textarea>" in html

    def test_checkbox_label_after_control(self):
        field = FormFieldInfo(
            field_type=FormFieldType.CHECKBOX,
            label="I agree",
            anchor_text="I agree",
            options=[FormFieldOption(label="I agree", checked=True)],
        )
        html = _render_form_field_html(field, "ff-p1-0")
        assert '<input type="checkbox" id="ff-p1-0"' in html
        assert "checked" in html
        # control comes before its label
        assert html.index("<input") < html.index("<label")

    def test_radio_group_uses_fieldset_legend_and_shared_name(self):
        field = FormFieldInfo(
            field_type=FormFieldType.RADIO_GROUP,
            label="Marital status",
            anchor_text="Marital status",
            options=[
                FormFieldOption(label="Single"),
                FormFieldOption(label="Married", checked=True),
            ],
        )
        html = _render_form_field_html(field, "ff-p1-0")
        assert "<fieldset>" in html
        assert "<legend>Marital status</legend>" in html
        # both radios share the group name
        assert html.count('name="ff-p1-0"') == 2
        assert '<input type="radio" id="ff-p1-0-0"' in html
        assert '<input type="radio" id="ff-p1-0-1"' in html
        assert "checked" in html

    def test_checkbox_group_uses_distinct_names(self):
        field = FormFieldInfo(
            field_type=FormFieldType.CHECKBOX_GROUP,
            label="Toppings",
            anchor_text="Toppings",
            options=[FormFieldOption(label="Cheese"), FormFieldOption(label="Olives")],
        )
        html = _render_form_field_html(field, "ff-p1-0")
        assert "<fieldset>" in html
        assert 'name="ff-p1-0-0"' in html
        assert 'name="ff-p1-0-1"' in html

    def test_select_renders_options(self):
        field = FormFieldInfo(
            field_type=FormFieldType.SELECT,
            label="State",
            anchor_text="State",
            options=[
                FormFieldOption(label="Illinois", checked=True),
                FormFieldOption(label="Iowa"),
            ],
        )
        html = _render_form_field_html(field, "ff-p1-0")
        assert '<select id="ff-p1-0"' in html
        assert "<option selected>Illinois</option>" in html
        assert "<option>Iowa</option>" in html

    def test_signature_field(self):
        field = FormFieldInfo(field_type=FormFieldType.SIGNATURE, label="Signature", anchor_text="Sign")
        html = _render_form_field_html(field, "ff-p1-0")
        assert "(signature)" in html

    def test_option_labels_are_html_escaped(self):
        field = FormFieldInfo(
            field_type=FormFieldType.SELECT,
            label="Pick <one>",
            anchor_text="Pick",
            options=[FormFieldOption(label="A & B")],
        )
        html = _render_form_field_html(field, "ff-p1-0")
        assert "Pick &lt;one&gt;" in html
        assert "A &amp; B" in html


# ---------------------------------------------------------------------------
# _apply_form_field — anchor replacement
# ---------------------------------------------------------------------------


class TestApplyFormField:
    def test_replaces_matching_anchor_line(self):
        md = "# Application\n\nName: ____________\n\nThanks"
        field = FormFieldInfo(
            field_type=FormFieldType.TEXT,
            label="Full name",
            anchor_text="Name: ____________",
            page=1,
        )
        new_md, change = _apply_form_field(md, field, "ff-p1-0")
        assert change is not None
        assert "Name: ____________" not in new_md
        assert '<input type="text" id="ff-p1-0"' in new_md
        # surrounding lines preserved
        assert new_md.startswith("# Application")
        assert new_md.endswith("Thanks")
        assert change.stage == "form_field"

    def test_missing_anchor_returns_original(self):
        md = "# Application\n\nNothing to fill in here."
        field = FormFieldInfo(
            field_type=FormFieldType.TEXT,
            label="Full name",
            anchor_text="zzz totally absent anchor zzz",
            page=1,
        )
        new_md, change = _apply_form_field(md, field, "ff-p1-0")
        assert change is None
        assert new_md == md


# ---------------------------------------------------------------------------
# _step_form_fields — orchestration
# ---------------------------------------------------------------------------


def _mock_agent_returning(output: FormFieldsPageOutput) -> MagicMock:
    mock_agent = MagicMock()
    mock_run_result = MagicMock()
    mock_run_result.output = output
    mock_run_result.usage.return_value = MagicMock(request_tokens=10, response_tokens=5)
    mock_agent.run = AsyncMock(return_value=mock_run_result)
    return mock_agent


def _patches():
    return (
        patch("pydantic_ai.Agent"),
        patch("pydantic_ai.models.bedrock.BedrockConverseModel"),
        patch("pydantic_ai.messages.BinaryContent"),
    )


class TestStepFormFields:
    @pytest.mark.asyncio
    async def test_injects_detected_field_and_rebuilds_version(self, service, base_result):
        output = FormFieldsPageOutput(
            form_fields=[
                FormFieldInfo(
                    field_type=FormFieldType.TEXT,
                    label="Full name",
                    anchor_text="Name: ____________",
                )
            ]
        )
        mock_agent = _mock_agent_returning(output)
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result)

        page_md = base_result.page_markdowns["v0"]["1"]
        assert '<input type="text" id="ff-p1-0"' in page_md
        # full version rebuilt from the corrected page
        assert '<input type="text" id="ff-p1-0"' in base_result.versions["v0"]

        step = next(s for s in base_result.steps if s.name == "form_fields")
        assert step.display_name == "Form Fields"
        assert step.metadata["fields_detected"] == 1
        assert step.metadata["fields_injected"] == 1
        assert len(step.changes) == 1

    @pytest.mark.asyncio
    async def test_no_fields_records_empty_step(self, service, base_result):
        mock_agent = _mock_agent_returning(FormFieldsPageOutput(form_fields=[]))
        original = base_result.versions["v0"]
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result)

        step = next(s for s in base_result.steps if s.name == "form_fields")
        assert step.metadata["fields_detected"] == 0
        assert step.metadata["fields_injected"] == 0
        assert step.changes == []
        # markdown untouched
        assert base_result.versions["v0"] == original

    @pytest.mark.asyncio
    async def test_detected_but_anchor_missing_is_not_injected(self, service, base_result):
        output = FormFieldsPageOutput(
            form_fields=[
                FormFieldInfo(
                    field_type=FormFieldType.TEXT,
                    label="Phantom",
                    anchor_text="zzz absent anchor zzz",
                )
            ]
        )
        mock_agent = _mock_agent_returning(output)
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result)

        step = next(s for s in base_result.steps if s.name == "form_fields")
        assert step.metadata["fields_detected"] == 1
        assert step.metadata["fields_injected"] == 0

    @pytest.mark.asyncio
    async def test_agent_failure_skips_page_without_raising(self, service, base_result):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM error"))
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result)

        step = next(s for s in base_result.steps if s.name == "form_fields")
        assert step.metadata["fields_injected"] == 0
