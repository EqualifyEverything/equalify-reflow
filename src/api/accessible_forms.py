"""Accessible form endpoints.

Turns a PDF form into an accessible HTML form a screen-reader/voice-tool user can
complete independently, and writes submitted values back into the original PDF so
the final document is visually identical to the official form.

Flow:
1. POST /api/v1/forms/accessible/html  — upload the PDF (plus optionally the Form
   Field Mapper metadata) and get back a WCAG-conformant HTML form.
2. POST /api/v1/forms/accessible/fill  — upload the same PDF plus a JSON object of
   field-name -> value and get back the filled PDF.

The field-name keys in the submitted values are exactly the ``name`` attributes
rendered in step 1, so the round-trip is direct.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import HTMLResponse

from ..dependencies import get_s3_url_service, get_storage_service
from ..services import S3URLService, StorageService, accessible_form_store
from ..services.accessible_form import (
    build_form,
    extract_acroform_fields,
    fill_pdf_form,
    overlay_answers_on_pdf,
    render_accessible_form_html,
    render_responses_pdf,
)
from ..services.accessible_form_builder import build_accessible_form_from_pdf

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/forms", tags=["Accessible Forms"])

# Public, browser-facing router. Non-/api path => exempt from API-key auth, so the
# served form can be opened and submitted same-origin from a browser with no key.
public_router = APIRouter(prefix="/accessible-forms", tags=["Accessible Forms (browser)"])


def _read_pdf_or_400(content: bytes, filename: str | None) -> None:
    if not filename or not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are supported")
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty file")


def _parse_field_map(field_map: str | None) -> dict[str, Any] | None:
    if not field_map:
        return None
    try:
        data = json.loads(field_map)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"field_map is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="field_map must be a JSON object")
    return data


@router.post("/accessible/html")
async def generate_accessible_form(
    file: UploadFile = File(...),
    field_map: str | None = Form(
        default=None,
        description="Optional Form Field Mapper 'metadata' JSON (adds plain-language descriptions/sections).",
    ),
    title: str | None = Form(default=None, description="Override the form title."),
    instructions: str = Form(default="", description="Intro text shown at the top of the form."),
    as_json: bool = Form(default=False, description="Return {form, html} JSON instead of an HTML page."),
) -> Response:
    """Generate a WCAG-conformant HTML form from a PDF's AcroForm fields.

    The PDF's real fields are the source of truth (so write-back is exact); the
    optional field_map only enriches labels/descriptions/sections.
    """
    content = await file.read()
    _read_pdf_or_400(content, file.filename)

    try:
        acro_fields = extract_acroform_fields(content)
    except RuntimeError as e:  # pypdf not installed
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    fm = _parse_field_map(field_map)
    doc_title = title or (fm or {}).get("document_kind") or (file.filename or "Form").rsplit(".", 1)[0]

    form = build_form(
        title=str(doc_title),
        acroform_fields=acro_fields,
        field_map=fm,
        instructions=instructions,
    )

    if not form.fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No fillable fields found. This PDF has no AcroForm fields; pass the Form "
                "Field Mapper metadata as field_map to render an inferred form instead."
            ),
        )

    rendered = render_accessible_form_html(form, action_url="/api/v1/forms/accessible/fill")

    if as_json:
        return Response(
            content=json.dumps({"form": form.model_dump(), "html": rendered}),
            media_type="application/json",
        )
    return HTMLResponse(content=rendered)


@router.post("/accessible/from-pdf")
async def accessible_form_from_pdf(
    file: UploadFile = File(...),
    enrich: bool = Form(
        default=True,
        description="Run the Form Field Mapper agent (Docling + LLM) to add plain-language descriptions. Set false for AcroForm-only.",
    ),
    title: str | None = Form(default=None, description="Override the form title."),
    instructions: str = Form(default="", description="Intro text shown at the top of the form."),
    as_json: bool = Form(default=False, description="Return {form, html} JSON instead of an HTML page."),
) -> Response:
    """End-to-end: upload a PDF, get back the accessible HTML form.

    Reads the PDF's authoritative AcroForm fields and, when ``enrich`` is true,
    layers on the Form Field Mapper agent's plain-language descriptions in a
    single call. Degrades to AcroForm-only if enrichment is unavailable.
    """
    content = await file.read()
    _read_pdf_or_400(content, file.filename)

    try:
        form = await build_accessible_form_from_pdf(
            content, enrich=enrich, title=title, instructions=instructions,
            source_name=file.filename,
        )
    except RuntimeError as e:  # pypdf not installed
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    if not form.fields:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No fillable fields found. If this is a flat or scanned form, retry with enrich=true so the fields can be inferred.",
        )

    rendered = render_accessible_form_html(form, action_url="/api/v1/forms/accessible/fill")
    if as_json:
        return Response(
            content=json.dumps({"form": form.model_dump(), "html": rendered}),
            media_type="application/json",
        )
    return HTMLResponse(content=rendered)


@router.post("/accessible/fill")
async def fill_accessible_form(
    file: UploadFile = File(..., description="The ORIGINAL PDF to fill."),
    values: str = Form(..., description="JSON object mapping PDF field names to submitted values."),
) -> Response:
    """Write submitted values into the original PDF and return the filled PDF."""
    content = await file.read()
    _read_pdf_or_400(content, file.filename)

    try:
        data = json.loads(values)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"values is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values must be a JSON object")

    try:
        filled = fill_pdf_form(content, data)
    except RuntimeError as e:  # pypdf not installed
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))

    out_name = (file.filename or "form.pdf").rsplit(".", 1)[0] + "-filled.pdf"
    return Response(
        content=filled,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )


# ---------------------------------------------------------------------------
# Browser flow: prepare (store PDF) -> view (served form) -> fill (write-back)
# ---------------------------------------------------------------------------

@router.post("/accessible/prepare")
async def prepare_accessible_form(
    file: UploadFile = File(...),
    enrich: bool = Form(default=False, description="Run the Form Field Mapper agent for plain-language descriptions."),
    title: str | None = Form(default=None),
    instructions: str = Form(
        default="Fill in the fields below. Your answers are written into the official PDF, which keeps its exact appearance.",
    ),
) -> Response:
    """Store a PDF + its accessible form and return a browser URL to fill it.

    POST the PDF here with your API key, then open the returned ``view_url`` in a
    browser to fill the form and download the populated PDF.
    """
    content = await file.read()
    _read_pdf_or_400(content, file.filename)
    try:
        form = await build_accessible_form_from_pdf(
            content, enrich=enrich, title=title, instructions=instructions, source_name=file.filename,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e))
    if not form.fields:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No fillable fields found. If this is a flat or scanned form, retry with enrich=true so the fields can be inferred.")
    form_id = accessible_form_store.put(content, form)
    return Response(
        content=json.dumps({
            "id": form_id,
            "view_url": f"/accessible-forms/view/{form_id}",
            "fields": len(form.fields),
            "title": form.title,
        }),
        media_type="application/json",
    )


@router.post("/upload")
async def upload_pdf_for_url(
    file: UploadFile = File(...),
    storage: StorageService = Depends(get_storage_service),
    url_service: S3URLService = Depends(get_s3_url_service),
) -> Response:
    """Upload a local PDF and get back a public URL (stored in the S3 temp bucket).

    For the local-file / bookmarklet case: a PDF that isn't already web-reachable
    can be uploaded here to mint a URL a downstream consumer can fetch. Uses
    Reflow's own S3 storage (Floci in dev, real S3 in prod).
    """
    content = await file.read()
    _read_pdf_or_400(content, file.filename)
    await file.seek(0)  # store_document re-reads the stream
    job_id, s3_key = await storage.store_document(file)
    url = await url_service.generate_url(s3_key, bucket=url_service.temp_bucket, expiration=3600)
    return Response(
        content=json.dumps({"id": job_id, "s3_key": s3_key, "url": url, "expires_in": 3600}),
        media_type="application/json",
    )


@public_router.get("/view/{form_id}")
async def view_accessible_form(form_id: str) -> Response:
    """Serve the accessible form for a prepared PDF (open this in a browser)."""
    entry = accessible_form_store.get(form_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found or expired. Run /api/v1/forms/accessible/prepare again.",
        )
    _pdf, form = entry
    html = render_accessible_form_html(form, action_url=f"/accessible-forms/fill/{form_id}")
    return HTMLResponse(content=html)


@public_router.post("/fill/{form_id}")
async def submit_accessible_form(form_id: str, request: Request) -> Response:
    """Receive the browser form submission and return the filled original PDF."""
    entry = accessible_form_store.get(form_id)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Form not found or expired. Run /api/v1/forms/accessible/prepare again.",
        )
    pdf_bytes, form = entry
    submitted = await request.form()
    values = {fld.pdf_field: submitted.get(fld.pdf_field) for fld in form.fields}
    base = (form.title or "form").strip().replace(" ", "_")[:80]
    if form.source == "agent":
        # Scanned/flat forms: we generate a clean accessible form from the content
        # (no overlay onto the scan), so answers are captured as a responses doc.
        out = render_responses_pdf(form, values)
        out_name = f"{base}-responses.pdf"
    else:
        out = fill_pdf_form(pdf_bytes, values)
        out_name = f"{base}-filled.pdf"
    return Response(
        content=out,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{out_name}"'},
    )
