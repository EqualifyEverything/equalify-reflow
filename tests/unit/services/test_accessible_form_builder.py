"""End-to-end wiring tests for the PDF -> accessible form builder.

The live Docling extraction and Form Field Mapper agent are injected as fakes, so
these tests exercise the real extraction + merge + render path without a model
stack. The AcroForm read needs ``pypdf``; ``reportlab`` synthesises the sample
form. Both are skipped if absent.
"""

from __future__ import annotations

import asyncio
from io import BytesIO

import pytest

from src.agents.prompts.form_field_mapper import FormFieldEntry, FormFieldMapOutput
from src.services.accessible_form import render_accessible_form_html
from src.services.accessible_form_builder import build_accessible_form_from_pdf


def _sample_pdf() -> bytes:
    pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    form = c.acroForm
    form.textfield(name="passenger_name", tooltip="Passenger full legal name",
                   x=150, y=745, width=220, height=16, forceBorder=True)
    form.checkbox(name="agree", tooltip="I agree to the terms", x=150, y=700, size=16,
                  fieldFlags="required", forceBorder=True)
    c.save()
    return buf.getvalue()


def _fake_output() -> FormFieldMapOutput:
    return FormFieldMapOutput(
        document_kind="Service Animal Form",
        overall_confidence="high",
        fields=[
            FormFieldEntry(label="Passenger full legal name", field_type="text",
                           description="As printed on your government ID.",
                           suggested_key="passenger_name", section="Passenger"),
            FormFieldEntry(label="I agree to the terms", field_type="checkbox",
                           description="You must agree to proceed.",
                           suggested_key="agree", section="Consent"),
        ],
    )


class TestBuildFromPdf:
    def test_enrich_merges_agent_descriptions(self):
        pdf = _sample_pdf()

        async def fake_extract(_b, **_k):
            return "# Service Animal Form\n\nName ____\nAgree [ ]"

        async def fake_mapper(_md, **_k):
            return _fake_output()

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=True, extract_text=fake_extract, run_mapper=fake_mapper,
        ))
        assert form.source == "merged"
        assert form.title == "Service Animal Form"
        pn = next(f for f in form.fields if f.pdf_field == "passenger_name")
        assert pn.description == "As printed on your government ID."
        assert pn.section == "Passenger"
        # The authoritative AcroForm label/type win; agent text is layered on.
        assert pn.label == "Passenger full legal name"
        html = render_accessible_form_html(form)
        assert "As printed on your government ID." in html

    def test_enrich_false_skips_agent(self):
        pdf = _sample_pdf()

        async def boom(*_a, **_k):
            raise AssertionError("agent/extraction must not be called when enrich=False")

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=False, extract_text=boom, run_mapper=boom,
        ))
        assert len(form.fields) == 2
        assert all(f.description == "" for f in form.fields)

    def test_enrich_failure_degrades_to_acroform(self):
        pdf = _sample_pdf()

        async def fake_extract(_b, **_k):
            return "irrelevant"

        async def failing_mapper(_md, **_k):
            raise RuntimeError("LLM down")

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=True, extract_text=fake_extract, run_mapper=failing_mapper,
        ))
        # Still produces a usable form from the authoritative AcroForm fields.
        assert len(form.fields) == 2
        assert any(f.label == "Passenger full legal name" for f in form.fields)


def _flat_pdf() -> bytes:
    """A PDF with text but NO AcroForm fields (a flat/printed form)."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 720, "Name")
    c.drawString(72, 700, "Date of Birth")
    c.save()
    return buf.getvalue()


class TestFieldlessEnrichment:
    """Fieldless forms: deterministic discovery + best-effort agent descriptions."""

    def test_descriptions_attach_to_parsed_fields(self):
        pytest.importorskip("pypdf")
        pdf = _flat_pdf()

        async def fake_extract(_b, **_k):
            return "## Adult Form\n\nName\n\nDate of Birth\n"

        async def fake_mapper(_md, **_k):
            return FormFieldMapOutput(
                document_kind="Adult Form",
                overall_confidence="high",
                fields=[
                    FormFieldEntry(label="Name", field_type="text",
                                   description="The person's full name.", suggested_key="name"),
                    FormFieldEntry(label="Date of Birth", field_type="date",
                                   description="The person's date of birth.", suggested_key="dob"),
                ],
            )

        async def no_vision(_b):
            return []  # force the OCR-parse fallback path

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=True, extract_text=fake_extract, run_mapper=fake_mapper,
            extract_vision=no_vision,
        ))
        assert form.source == "agent"  # fieldless
        by_label = {f.label: f for f in form.fields}
        assert by_label["Name"].description == "The person's full name."
        assert by_label["Date of Birth"].description == "The person's date of birth."


class TestVisionExtraction:
    """When vision returns positioned fields, the builder uses them (enables overlay)."""

    def test_vision_fields_take_precedence(self):
        pytest.importorskip("pypdf")
        from src.services.accessible_form import AccessibleFormField

        pdf = _flat_pdf()

        async def fake_vision(_b):
            return [
                AccessibleFormField(pdf_field="name", control="text", label="Name",
                                    page=0, box=[0.3, 0.10, 0.6, 0.13]),
                AccessibleFormField(pdf_field="dob", control="date", label="Date of Birth",
                                    page=0, box=[0.3, 0.15, 0.6, 0.18]),
            ]

        async def boom(*_a, **_k):
            raise AssertionError("OCR parse must not run when vision returns fields")

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=True, extract_vision=fake_vision, extract_text=boom,
        ))
        assert form.source == "agent"
        assert [f.label for f in form.fields] == ["Name", "Date of Birth"]
        assert form.fields[0].page == 0
        assert form.fields[0].box == [0.3, 0.10, 0.6, 0.13]


class TestVisionInformationalContent:
    """Vision can return informational reading content (leaflet/instruction pages)
    alongside or instead of fields, and the builder carries it onto the form."""

    def test_content_only_packet_builds_reading_document(self):
        pytest.importorskip("pypdf")
        from src.services.accessible_form import FormContentBlock
        from src.services.vision_form_extractor import VisionExtraction

        pdf = _flat_pdf()

        async def fake_vision(_b):
            return VisionExtraction(
                fields=[],
                content=[FormContentBlock(heading="Know Your Rights",
                                          paragraphs=["You may request repairs."], page=0)],
            )

        async def boom(*_a, **_k):
            raise AssertionError("OCR parse must not run when vision returns content")

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=True, extract_vision=fake_vision, extract_text=boom,
        ))
        assert form.source == "agent"
        assert form.fields == []
        assert [c.heading for c in form.content] == ["Know Your Rights"]

    def test_fields_and_content_are_both_carried(self):
        pytest.importorskip("pypdf")
        from src.services.accessible_form import AccessibleFormField, FormContentBlock
        from src.services.vision_form_extractor import VisionExtraction

        pdf = _flat_pdf()

        async def fake_vision(_b):
            return VisionExtraction(
                fields=[AccessibleFormField(pdf_field="county", control="text",
                                            label="County", section="Case Caption", page=0)],
                content=[FormContentBlock(heading="Leaflet", paragraphs=["Info."], page=1)],
            )

        async def boom(*_a, **_k):
            raise AssertionError("OCR parse must not run when vision returns content")

        form = asyncio.run(build_accessible_form_from_pdf(
            pdf, enrich=True, extract_vision=fake_vision, extract_text=boom,
        ))
        assert [f.label for f in form.fields] == ["County"]
        assert [c.heading for c in form.content] == ["Leaflet"]


class TestSectionQualification:
    """Multi-form packets disambiguate repeated captions by prefixing the form name."""

    def test_prefixes_sections_when_multiple_forms(self):
        from src.services.accessible_form import AccessibleFormField
        from src.services.vision_form_extractor import _qualify_sections_by_form

        fields = [
            AccessibleFormField(pdf_field="case_number", control="text",
                                label="Case Number", section="Case Caption", page=0),
            AccessibleFormField(pdf_field="case_number_2", control="text",
                                label="Case Number", section="Case Caption", page=1),
        ]
        _qualify_sections_by_form(fields, {0: "Motion and Affidavit", 1: "Complaint"})
        assert fields[0].section == "Motion and Affidavit — Case Caption"
        assert fields[1].section == "Complaint — Case Caption"

    def test_no_prefix_for_single_form(self):
        from src.services.accessible_form import AccessibleFormField
        from src.services.vision_form_extractor import _qualify_sections_by_form

        fields = [AccessibleFormField(pdf_field="county", control="text",
                                      label="County", section="Case Caption", page=0)]
        # Only one distinct form name -> sections left untouched.
        _qualify_sections_by_form(fields, {0: "Complaint", 1: "Complaint"})
        assert fields[0].section == "Case Caption"

    def test_uses_form_name_alone_when_section_empty(self):
        from src.services.accessible_form import AccessibleFormField
        from src.services.vision_form_extractor import _qualify_sections_by_form

        fields = [AccessibleFormField(pdf_field="x", control="text", label="X", section="", page=1)]
        _qualify_sections_by_form(fields, {0: "Motion", 1: "Complaint"})
        assert fields[0].section == "Complaint"


class TestPageRetry:
    """A transient failure or an empty return must not silently drop a page."""

    class _Out:
        def __init__(self, *, empty: bool):
            self.fields = [] if empty else ["f"]
            self.content = []
            self.page_kind = "form"
            self.form_name = ""

    class _Res:
        def __init__(self, out):
            self.output = out

    def test_retries_then_succeeds_after_transient_error(self):
        from src.services.vision_form_extractor import _run_page_with_retry

        class _Agent:
            calls = 0
            async def run(self, _msg):
                _Agent.calls += 1
                if _Agent.calls == 1:
                    raise RuntimeError("transient 429")
                return TestPageRetry._Res(TestPageRetry._Out(empty=False))

        ag = _Agent()
        out = asyncio.run(_run_page_with_retry(ag, ["m"], 0, 1, attempts=2, base_delay=0))
        assert out is not None and out.fields == ["f"]
        assert _Agent.calls == 2

    def test_returns_none_when_all_attempts_error(self):
        from src.services.vision_form_extractor import _run_page_with_retry

        class _Agent:
            calls = 0
            async def run(self, _msg):
                _Agent.calls += 1
                raise RuntimeError("down")

        ag = _Agent()
        out = asyncio.run(_run_page_with_retry(ag, ["m"], 0, 1, attempts=2, base_delay=0))
        assert out is None and _Agent.calls == 2

    def test_retries_once_on_empty_then_returns_content(self):
        from src.services.vision_form_extractor import _run_page_with_retry

        class _Agent:
            calls = 0
            async def run(self, _msg):
                _Agent.calls += 1
                return TestPageRetry._Res(TestPageRetry._Out(empty=(_Agent.calls == 1)))

        ag = _Agent()
        out = asyncio.run(_run_page_with_retry(ag, ["m"], 0, 1, attempts=2, base_delay=0))
        assert _Agent.calls == 2 and out.fields == ["f"]


class TestMarkerBlockMerge:
    """Thin numbered-step blocks get folded into their parent, deterministically."""

    def test_folds_numbered_steps_into_previous_block(self):
        from src.services.accessible_form import FormContentBlock
        from src.services.vision_form_extractor import _merge_marker_blocks

        content = [
            FormContentBlock(heading="Your Landlord's Obligations",
                             paragraphs=["The landlord must keep it safe."], page=3),
            FormContentBlock(heading="1) Go", paragraphs=["Go — tell the landlord in writing."], page=3),
            FormContentBlock(heading="2) Stay", paragraphs=["Stay and make repairs yourself."], page=3),
        ]
        out = _merge_marker_blocks(content)
        assert len(out) == 1
        assert out[0].heading == "Your Landlord's Obligations"
        # Step labels are preserved as paragraphs, and their text is carried over.
        assert "1) Go" in out[0].paragraphs and "2) Stay" in out[0].paragraphs
        assert any("tell the landlord" in p for p in out[0].paragraphs)

    def test_keeps_real_headings(self):
        from src.services.accessible_form import FormContentBlock
        from src.services.vision_form_extractor import _merge_marker_blocks

        content = [
            FormContentBlock(heading="Housing Cases Accepted", paragraphs=["Evictions"], page=2),
            FormContentBlock(heading="Q & A", paragraphs=["A question?"], page=2),
            FormContentBlock(heading="What appliances must my landlord fix?", paragraphs=["Heat."], page=2),
        ]
        out = _merge_marker_blocks(content)
        assert [b.heading for b in out] == [
            "Housing Cases Accepted", "Q & A", "What appliances must my landlord fix?",
        ]

    def test_does_not_merge_across_pages(self):
        from src.services.accessible_form import FormContentBlock
        from src.services.vision_form_extractor import _merge_marker_blocks

        content = [
            FormContentBlock(heading="Obligations", paragraphs=["x"], page=2),
            FormContentBlock(heading="1) Go", paragraphs=["y"], page=3),
        ]
        assert len(_merge_marker_blocks(content)) == 2

    def test_leading_marker_block_is_kept(self):
        from src.services.accessible_form import FormContentBlock
        from src.services.vision_form_extractor import _merge_marker_blocks

        out = _merge_marker_blocks([FormContentBlock(heading="1) Go", paragraphs=["y"], page=3)])
        assert len(out) == 1 and out[0].heading == "1) Go"
