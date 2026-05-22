"""Unit tests for the Form Fields step.

Covers the accessible-HTML renderer (`_render_form_field_html`), the
anchor-replacement injector (`_apply_form_field`), the deterministic form-signal
heuristic (`_page_has_form_signals`), and the `_step_form_fields` orchestration
(gating on has_forms OR heuristic, with the vision agent mocked).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from src.services.pipeline_viewer import (
    PipelineViewerService,
    _apply_form_field,
    _convert_task_list_checkboxes,
    _page_has_form_signals,
    _render_form_field_html,
)
from src.services.pipeline_viewer_models import (
    FormFieldInfo,
    FormFieldOption,
    FormFieldsPageOutput,
    FormFieldType,
    LayoutType,
    PageAttributes,
    PipelineViewerResult,
    StructureResult,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def service():
    return PipelineViewerService()


@pytest.fixture
def base_result():
    """A 1-page result whose newest per-page version is v0, with a form blank."""
    md = "# Application\n\nName: ____________"
    return PipelineViewerResult(
        filename="form.pdf",
        total_pages=1,
        versions={"v0": md},
        page_images={"1": "AAAA"},
        page_markdowns={"v0": {"1": md}},
        figures=[],
        steps=[],
        stats={},
    )


def _structure(has_forms: bool) -> StructureResult:
    return StructureResult(page_attributes={1: PageAttributes(layout=LayoutType.SINGLE_COLUMN, has_forms=has_forms)})


# ---------------------------------------------------------------------------
# _render_form_field_html
# ---------------------------------------------------------------------------


class TestRenderFormFieldHtml:
    def test_text_field_label_wired_to_input(self):
        field = FormFieldInfo(field_type=FormFieldType.TEXT, label="Full name", anchor_text="Name:")
        out = _render_form_field_html(field, "ff-p1-0")
        assert '<label for="ff-p1-0">Full name</label>' in out
        assert '<input type="text" id="ff-p1-0"' in out
        assert "readonly" in out

    def test_required_field_gets_aria_required(self):
        field = FormFieldInfo(
            field_type=FormFieldType.TEXT,
            label="Email",
            anchor_text="Email",
            required=True,
        )
        out = _render_form_field_html(field, "ff-p1-1")
        assert 'aria-required="true"' in out
        assert '<abbr title="required">*</abbr>' in out

    def test_date_field_uses_date_input(self):
        field = FormFieldInfo(field_type=FormFieldType.DATE, label="DOB", anchor_text="Date of birth")
        assert '<input type="date"' in _render_form_field_html(field, "ff-p1-0")

    def test_textarea_field(self):
        field = FormFieldInfo(field_type=FormFieldType.TEXTAREA, label="Comments", anchor_text="Comments")
        out = _render_form_field_html(field, "ff-p1-0")
        assert '<textarea id="ff-p1-0"' in out
        assert "</textarea>" in out

    def test_checkbox_label_after_control(self):
        field = FormFieldInfo(
            field_type=FormFieldType.CHECKBOX,
            label="I attest",
            anchor_text="I attest",
            options=[FormFieldOption(label="I attest", checked=True)],
        )
        out = _render_form_field_html(field, "ff-p1-0")
        assert '<input type="checkbox" id="ff-p1-0"' in out
        assert "checked" in out
        assert out.index("<input") < out.index("<label")

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
        out = _render_form_field_html(field, "ff-p1-0")
        assert "<fieldset>" in out
        assert "<legend>Marital status</legend>" in out
        assert out.count('name="ff-p1-0"') == 2
        assert '<input type="radio" id="ff-p1-0-0"' in out
        assert '<input type="radio" id="ff-p1-0-1"' in out

    def test_checkbox_group_uses_distinct_names(self):
        field = FormFieldInfo(
            field_type=FormFieldType.CHECKBOX_GROUP,
            label="Toppings",
            anchor_text="Toppings",
            options=[FormFieldOption(label="Cheese"), FormFieldOption(label="Olives")],
        )
        out = _render_form_field_html(field, "ff-p1-0")
        assert "<fieldset>" in out
        assert 'name="ff-p1-0-0"' in out
        assert 'name="ff-p1-0-1"' in out

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
        out = _render_form_field_html(field, "ff-p1-0")
        assert '<select id="ff-p1-0"' in out
        assert "<option selected>Illinois</option>" in out
        assert "<option>Iowa</option>" in out

    def test_signature_field(self):
        field = FormFieldInfo(field_type=FormFieldType.SIGNATURE, label="Signature", anchor_text="Sign")
        assert "(signature)" in _render_form_field_html(field, "ff-p1-0")

    def test_labels_html_escaped(self):
        field = FormFieldInfo(
            field_type=FormFieldType.SELECT,
            label="Pick <one>",
            anchor_text="Pick",
            options=[FormFieldOption(label="A & B")],
        )
        out = _render_form_field_html(field, "ff-p1-0")
        assert "Pick &lt;one&gt;" in out
        assert "A &amp; B" in out


# ---------------------------------------------------------------------------
# _apply_form_field
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
# _convert_task_list_checkboxes — no markdown inputs survive
# ---------------------------------------------------------------------------


class TestConvertTaskListCheckboxes:
    def test_gfm_task_list_becomes_accessible_html(self):
        md = "- [ ] I attest the animal is vaccinated"
        out, changes = _convert_task_list_checkboxes(md, 1)
        assert "- [ ]" not in out
        assert '<input type="checkbox" id="cb-p1-0"' in out
        assert "disabled>" in out
        assert '<label for="cb-p1-0">I attest the animal is vaccinated</label>' in out
        assert len(changes) == 1
        assert changes[0].stage == "form_field"

    def test_checked_task_item_is_checked(self):
        out, _ = _convert_task_list_checkboxes("- [x] Done", 2)
        assert " checked disabled>" in out

    def test_leading_glyph_stripped_from_label(self):
        # Docling emits both the task-list marker AND a glyph
        out, _ = _convert_task_list_checkboxes("- [ ] ☐ I agree", 1)
        assert '<label for="cb-p1-0">I agree</label>' in out
        assert "☐" not in out

    def test_label_html_escaped(self):
        out, _ = _convert_task_list_checkboxes("- [ ] a < b & c", 1)
        assert "a &lt; b &amp; c" in out

    def test_non_task_lines_untouched(self):
        md = "# Heading\n\n- a normal bullet\n\nprose"
        out, changes = _convert_task_list_checkboxes(md, 1)
        assert out == md
        assert changes == []


# ---------------------------------------------------------------------------
# _page_has_form_signals — deterministic detection
# ---------------------------------------------------------------------------


class TestPageHasFormSignals:
    def test_long_underline_run_detected(self):
        assert _page_has_form_signals("Name: ____________")

    def test_checkbox_glyph_detected(self):
        assert _page_has_form_signals("☐ I attest the animal is vaccinated")

    def test_bracket_checkbox_detected(self):
        assert _page_has_form_signals("[ ] Option A")

    def test_short_underscores_not_a_signal(self):
        # snake_case / a couple underscores shouldn't trip it
        assert not _page_has_form_signals("see my_var and a__b in the code")

    def test_plain_prose_has_no_signals(self):
        assert not _page_has_form_signals("# Heading\n\nJust some ordinary text.")


# ---------------------------------------------------------------------------
# _step_form_fields — gating + orchestration
# ---------------------------------------------------------------------------


def _mock_agent_returning(output: FormFieldsPageOutput) -> MagicMock:
    mock_agent = MagicMock()
    run_result = MagicMock()
    run_result.output = output
    run_result.usage.return_value = MagicMock(request_tokens=10, response_tokens=5)
    mock_agent.run = AsyncMock(return_value=run_result)
    return mock_agent


def _patches():
    return (
        patch("pydantic_ai.Agent"),
        patch("pydantic_ai.models.bedrock.BedrockConverseModel"),
        patch("pydantic_ai.messages.BinaryContent"),
    )


_FIELD_OUT = FormFieldsPageOutput(
    form_fields=[
        FormFieldInfo(
            field_type=FormFieldType.TEXT,
            label="Full name",
            anchor_text="Name: ____________",
        )
    ]
)


class TestStepFormFields:
    @pytest.mark.asyncio
    async def test_fires_via_heuristic_even_when_has_forms_false(self, service, base_result):
        """has_forms=False but underscores present → still processed (heuristic)."""
        mock_agent = _mock_agent_returning(_FIELD_OUT)
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result, _structure(has_forms=False))

        assert mock_agent.run.await_count == 1  # the page was processed
        assert '<input type="text" id="ff-p1-0"' in base_result.versions["v0"]
        step = next(s for s in base_result.steps if s.name == "form_fields")
        assert step.metadata["pages_processed"] == 1
        assert step.metadata["fields_injected"] == 1

    @pytest.mark.asyncio
    async def test_fires_via_has_forms_flag(self, service, base_result):
        mock_agent = _mock_agent_returning(_FIELD_OUT)
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result, _structure(has_forms=True))
        assert mock_agent.run.await_count == 1

    @pytest.mark.asyncio
    async def test_acroform_doc_scans_page_with_no_text_signals(self, service):
        """Docling dropped the field text, but the doc has AcroForm widgets →
        the page is still scanned (this is the multi-page-form fix)."""
        md = "# Section B\n\nAnimal's Name"  # no underscores/checkboxes survived
        result = PipelineViewerResult(
            filename="form.pdf",
            total_pages=1,
            versions={"v0": md},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": md}},
            stats={"form_field_count": 24},
        )
        mock_agent = _mock_agent_returning(FormFieldsPageOutput(form_fields=[]))
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(result, _structure(has_forms=False))

        assert mock_agent.run.await_count == 1  # scanned despite no text signals
        step = next(s for s in result.steps if s.name == "form_fields")
        assert step.metadata["pages_processed"] == 1

    @pytest.mark.asyncio
    async def test_skips_page_in_non_form_document(self, service):
        md = "# Notes\n\nJust prose, nothing to fill in."
        result = PipelineViewerResult(
            filename="x.pdf",
            total_pages=1,
            versions={"v0": md},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": md}},
        )
        mock_agent = _mock_agent_returning(FormFieldsPageOutput(form_fields=[]))
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(result, _structure(has_forms=False))

        assert mock_agent.run.await_count == 0  # never invoked the model
        step = next(s for s in result.steps if s.name == "form_fields")
        assert step.metadata["pages_processed"] == 0
        assert result.versions["v0"] == md

    @pytest.mark.asyncio
    async def test_markdown_checkbox_converted_even_when_agent_finds_nothing(self, service):
        """A GFM task-list checkbox must become HTML even if the vision agent
        returns no fields — the deterministic safeguard."""
        md = "# Form\n\n- [ ] I attest the animal is vaccinated"
        result = PipelineViewerResult(
            filename="form.pdf",
            total_pages=1,
            versions={"v0": md},
            page_images={"1": "AAAA"},
            page_markdowns={"v0": {"1": md}},
        )
        mock_agent = _mock_agent_returning(FormFieldsPageOutput(form_fields=[]))
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(result, _structure(has_forms=False))

        v0 = result.versions["v0"]
        assert "- [ ]" not in v0
        assert '<input type="checkbox"' in v0
        step = next(s for s in result.steps if s.name == "form_fields")
        assert step.metadata["markdown_checkboxes_converted"] == 1

    @pytest.mark.asyncio
    async def test_agent_failure_does_not_raise(self, service, base_result):
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("LLM error"))
        p1, p2, p3 = _patches()
        with p1 as agent_cls, p2, p3:
            agent_cls.return_value = mock_agent
            await service._step_form_fields(base_result, _structure(has_forms=True))
        step = next(s for s in base_result.steps if s.name == "form_fields")
        assert step.metadata["fields_injected"] == 0
