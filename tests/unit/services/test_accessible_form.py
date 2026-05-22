"""Unit tests for the accessible form generator and PDF write-back.

The HTML render tests are pure standard library and always run. The AcroForm
extraction and fill round-trip need ``pypdf`` (read/write) and ``reportlab``
(to synthesise a sample form); those are skipped if the libs are absent so the
fast suite stays green until ``uv add pypdf`` lands.
"""

from __future__ import annotations

from io import BytesIO

import pytest

from src.services.accessible_form import (
    AccessibleForm,
    AccessibleFormField,
    FormContentBlock,
    FormOption,
    build_form,
    render_accessible_form_html,
)


# ---------------------------------------------------------------------------
# Pure render tests (no third-party deps)
# ---------------------------------------------------------------------------

class TestRenderAccessibility:
    def _form(self) -> AccessibleForm:
        return AccessibleForm(
            title="DOT Service Animal Form",
            instructions="Complete all required fields.",
            fields=[
                AccessibleFormField(pdf_field="passenger_name", control="text",
                                    label="Passenger name", description="As printed on your ID.",
                                    required=True, section="Passenger"),
                AccessibleFormField(pdf_field="agree", control="checkbox",
                                    label="I agree to the terms", required=True, section="Consent"),
                AccessibleFormField(pdf_field="animal_type", control="radio",
                                    label="Service animal type", required=True, section="Animal",
                                    options=[FormOption(value="/Dog", label="Dog"),
                                             FormOption(value="/MiniHorse", label="Miniature horse")]),
                AccessibleFormField(pdf_field="state", control="select", label="State",
                                    options=[FormOption(value="IL", label="Illinois"),
                                             FormOption(value="OH", label="Ohio")], section="Passenger"),
            ],
        )

    def test_document_has_lang_title_and_skip_link(self):
        html = render_accessible_form_html(self._form())
        assert '<html lang="en">' in html
        assert "<title>DOT Service Animal Form</title>" in html
        assert 'class="skip-link"' in html

    def test_every_control_is_labelled(self):
        html = render_accessible_form_html(self._form())
        # text + checkbox + select have label[for]; radio uses fieldset/legend.
        assert html.count("<label for=") >= 3
        assert "<fieldset" in html and "<legend>" in html

    def test_required_is_conveyed_in_text_and_aria(self):
        html = render_accessible_form_html(self._form())
        assert 'aria-required="true"' in html
        assert "required" in html  # sr-only "required" text
        assert 'role="alert"' in html  # error summary region

    def test_help_text_wired_with_aria_describedby(self):
        html = render_accessible_form_html(self._form())
        assert "aria-describedby=" in html
        assert "As printed on your ID." in html

    def test_control_name_is_pdf_field_for_writeback(self):
        html = render_accessible_form_html(self._form())
        for name in ("passenger_name", "agree", "animal_type", "state"):
            assert f'name="{name}"' in html

    def test_dynamic_text_is_escaped(self):
        evil = AccessibleForm(
            title="X",
            fields=[AccessibleFormField(pdf_field="x", control="text",
                                        label="<script>alert(1)</script>")],
        )
        html = render_accessible_form_html(evil)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_fields_only_form_is_unchanged_by_content_feature(self):
        # A form with no informational content renders with the <form> and submit
        # control, exactly as before (no reading regions).
        html = render_accessible_form_html(self._form())
        assert 'class="reading"' not in html
        assert "<form" in html
        assert "Review and download filled PDF" in html

    def test_build_form_from_agent_map_when_no_acroform(self):
        fm = {
            "document_kind": "Intake Form",
            "fields": [
                {"label": "Full name", "field_type": "text", "description": "Your name",
                 "suggested_key": "full_name", "required": True, "section": "About you"},
                {"label": "Email", "field_type": "email", "suggested_key": "email"},
            ],
        }
        form = build_form(title="Intake Form", acroform_fields=[], field_map=fm)
        assert form.source == "agent"
        assert [f.pdf_field for f in form.fields] == ["full_name", "email"]
        email = next(f for f in form.fields if f.pdf_field == "email")
        assert email.control == "email"


class TestInformationalContent:
    """Informational pages (leaflets, FAQs) surface as reading regions, not dropped."""

    def test_content_blocks_render_as_reading_regions(self):
        form = AccessibleForm(
            title="Tenant Packet",
            fields=[AccessibleFormField(pdf_field="county", control="text", label="County",
                                        section="Case Caption", page=0)],
            content=[FormContentBlock(
                heading="Your Landlord's Obligations",
                paragraphs=["The landlord must keep the unit safe.",
                            "Repairs must be made in a reasonable time."],
                page=1)],
        )
        html = render_accessible_form_html(form)
        assert 'class="reading"' in html
        assert "Your Landlord" in html
        assert "The landlord must keep the unit safe." in html
        # The reading region carries no input controls.
        reading = html.split('class="reading"', 1)[1].split("</section>", 1)[0]
        assert "<input" not in reading and "<textarea" not in reading and "<select" not in reading

    def test_fields_precede_later_page_content_in_reading_order(self):
        form = AccessibleForm(
            title="Packet",
            fields=[AccessibleFormField(pdf_field="county", control="text", label="County",
                                        section="Case Caption", page=0)],
            content=[FormContentBlock(heading="Leaflet", paragraphs=["Info text."], page=1)],
        )
        html = render_accessible_form_html(form)
        assert html.index('name="county"') < html.index("Leaflet")

    def test_pure_informational_packet_has_no_form_controls(self):
        form = AccessibleForm(
            title="Rights Leaflet",
            content=[FormContentBlock(heading="Know Your Rights",
                                      paragraphs=["You have the right to repairs."], page=0)],
        )
        html = render_accessible_form_html(form)
        assert "<form" not in html
        assert "Review and download filled PDF" not in html
        assert "Know Your Rights" in html and "You have the right to repairs." in html

    def test_content_text_is_escaped(self):
        form = AccessibleForm(
            title="X",
            content=[FormContentBlock(heading="<b>h</b>",
                                      paragraphs=["<script>alert(1)</script>"], page=0)],
        )
        html = render_accessible_form_html(form)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# AcroForm extraction + fill round-trip (needs pypdf + reportlab)
# ---------------------------------------------------------------------------

def _sample_pdf() -> bytes:
    reportlab = pytest.importorskip("reportlab")  # noqa: F841
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    form = c.acroForm
    form.textfield(name="passenger_name", tooltip="Passenger full legal name",
                   x=150, y=745, width=220, height=16, forceBorder=True)
    form.textfield(name="email", tooltip="Email address", x=150, y=710, width=220, height=16,
                   fieldFlags="required", forceBorder=True)
    form.checkbox(name="agree", tooltip="I agree to the terms", x=150, y=678, size=16,
                  fieldFlags="required", forceBorder=True)
    form.radio(name="animal_type", tooltip="Service animal type", value="Dog", selected=False,
               x=150, y=645, size=14, forceBorder=True)
    form.radio(name="animal_type", value="MiniHorse", selected=False, x=250, y=645, size=14,
               forceBorder=True)
    form.choice(name="state", tooltip="State of residence", value="IL", options=["IL", "OH", "CA"],
                x=150, y=608, width=120, height=18, forceBorder=True)
    c.save()
    return buf.getvalue()


class TestAcroFormRoundTrip:
    def test_extract_fields(self):
        pytest.importorskip("pypdf")
        from src.services.accessible_form import extract_acroform_fields

        fields = {f.pdf_field: f for f in extract_acroform_fields(_sample_pdf())}
        assert fields["passenger_name"].control == "text"
        assert fields["passenger_name"].label == "Passenger full legal name"
        assert fields["email"].required is True
        assert fields["agree"].control == "checkbox"
        assert fields["animal_type"].control == "radio" and len(fields["animal_type"].options) >= 2
        assert fields["state"].control == "select" and len(fields["state"].options) >= 3

    def test_fill_round_trip(self):
        pypdf = pytest.importorskip("pypdf")
        from src.services.accessible_form import fill_pdf_form

        filled = fill_pdf_form(_sample_pdf(), {
            "passenger_name": "Jane Q. Doe",
            "email": "jane@example.com",
            "agree": True,
            "animal_type": "Dog",
            "state": "OH",
        })
        vals = {k: v.get("/V") for k, v in pypdf.PdfReader(BytesIO(filled)).get_fields().items()}
        assert str(vals["passenger_name"]) == "Jane Q. Doe"
        assert str(vals["email"]) == "jane@example.com"
        assert str(vals["agree"]) not in ("/Off", "Off", "None", "")


class TestFieldsFromMarkdown:
    """Deterministic field discovery from OCR'd scanned-form markdown (no model)."""

    def test_parses_labels_checkboxes_and_choices(self):
        from src.services.accessible_form import fields_from_markdown

        md = (
            "<!-- image -->\n\n## ADULT INFORMATION FORM\n\n"
            "Name\n\nDate of Birth\n\nEmail:\n\nTelephone\n\n- [ ] Age\n\n"
            "Marital Status (circle one) M S D W\n\n"
            "| Date Started | Medication | Reason |\n\n"
            "By signing this form, I attest that I have read and authorize each item above.\n\n"
            "Describe the main problem that brought you in and what you hope to achieve in counseling.\n\n"
            "## FAMILY\n\nNumber of Marriages\n"
        )
        fields = fields_from_markdown(md)
        by_label = {f.label: f for f in fields}
        assert by_label["Name"].control == "text"
        assert by_label["Date of Birth"].control == "date"
        assert by_label["Email"].control == "email"
        assert by_label["Telephone"].control == "tel"
        assert by_label["Age"].control == "checkbox"
        ms = by_label["Marital Status"]
        assert ms.control == "radio"
        assert [o.value for o in ms.options] == ["M", "S", "D", "W"]
        assert by_label["Number of Marriages"].section == "FAMILY"

        labels = [f.label for f in fields]
        assert not any(la.startswith("|") or "Date Started" in la for la in labels)  # table row skipped
        assert not any(la.startswith("By signing") for la in labels)  # long policy skipped
        assert any(la.startswith("Describe") for la in labels)  # long fill-in prompt kept


class TestResponsesPdf:
    """Fieldless/scanned forms get a readable responses PDF instead of write-back."""

    def test_responses_pdf_is_valid_and_has_answers(self):
        pytest.importorskip("reportlab")
        from src.services.accessible_form import render_responses_pdf

        form = AccessibleForm(
            title="Adult Intake",
            source="agent",
            fields=[
                AccessibleFormField(pdf_field="full_name", control="text", label="Full name", section="About you"),
                AccessibleFormField(pdf_field="consent", control="checkbox", label="I consent", section="Consent"),
            ],
        )
        pdf = render_responses_pdf(form, {"full_name": "Jane Doe", "consent": "on"})
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 800  # produced a real document

    def test_responses_pdf_appends_informational_content(self):
        pytest.importorskip("reportlab")
        from src.services.accessible_form import render_responses_pdf

        base = AccessibleForm(
            title="Tenant Packet", source="agent",
            fields=[AccessibleFormField(pdf_field="county", control="text", label="County")],
        )
        with_content = base.model_copy(update={"content": [
            FormContentBlock(heading="Your Rights",
                             paragraphs=["You have the right to request repairs."] * 5, page=1),
        ]})
        without = render_responses_pdf(base, {"county": "Greenville"})
        appended = render_responses_pdf(with_content, {"county": "Greenville"})
        assert appended[:4] == b"%PDF"
        # The appendix adds real content, so the document grows.
        assert len(appended) > len(without)


class TestDuplicateLabelsAndMatching:
    """Fields that share a /TU label (e.g. four 'Phone' fields) must get distinct
    labels and the right per-field description."""

    def test_disambiguates_and_matches_descriptions(self):
        acro = [
            AccessibleFormField(pdf_field="Phone", control="tel", label="Phone"),
            AccessibleFormField(pdf_field="Phone (Veterinarian)", control="tel", label="Phone"),
            AccessibleFormField(pdf_field="Phone (Behavior Trainer)", control="tel", label="Phone"),
        ]
        field_map = {"fields": [
            {"label": "Handler phone", "suggested_key": "handler_phone", "description": "Handler phone."},
            {"label": "Veterinarian phone", "suggested_key": "veterinarian_phone", "description": "Vet phone."},
            {"label": "Behavior trainer phone", "suggested_key": "behavior_trainer_phone", "description": "Behavior trainer phone."},
        ]}
        form = build_form(title="T", acroform_fields=acro, field_map=field_map)

        labels = [f.label for f in form.fields]
        assert len(set(labels)) == 3  # no longer four identical "Phone" labels
        by_name = {f.pdf_field: f for f in form.fields}
        assert by_name["Phone (Veterinarian)"].description == "Vet phone."
        assert by_name["Phone (Behavior Trainer)"].description == "Behavior trainer phone."
        assert by_name["Phone"].description == "Handler phone."


class TestOverlay:
    """Vision-located answers are drawn onto the original page images."""

    def _one_page_pdf(self) -> bytes:
        pytest.importorskip("reportlab")
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.drawString(72, 720, "Name:")
        c.showPage()
        c.save()
        return buf.getvalue()

    def test_render_pdf_pages_returns_png(self):
        pytest.importorskip("pypdfium2")
        pytest.importorskip("PIL")
        from src.services.vision_form_extractor import render_pdf_pages

        pages = render_pdf_pages(self._one_page_pdf())
        assert len(pages) == 1
        assert pages[0][:8] == b"\x89PNG\r\n\x1a\n"

    def test_overlay_produces_valid_pdf(self):
        pytest.importorskip("pypdfium2")
        pytest.importorskip("PIL")
        from src.services.accessible_form import overlay_answers_on_pdf

        fields = [AccessibleFormField(pdf_field="name", control="text", label="Name",
                                      page=0, box=[0.2, 0.10, 0.5, 0.13])]
        out = overlay_answers_on_pdf(self._one_page_pdf(), fields, {"name": "Jane Doe"})
        assert out[:4] == b"%PDF"
        assert len(out) > 1000
