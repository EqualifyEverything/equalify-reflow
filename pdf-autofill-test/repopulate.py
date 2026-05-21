#!/usr/bin/env python3

import tempfile
from pathlib import Path

from flask import (
    Flask,
    request,
    send_file,
    render_template_string,
    redirect,
    url_for,
)

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, BooleanObject

app = Flask(__name__)

uploaded_pdfs = {}


def get_fields(pdf_path):
    reader = PdfReader(str(pdf_path))
    return reader.get_fields() or {}


def field_type(field):
    return str(field.get("/FT", ""))


def field_label(name, field):
    return field.get("/TU") or name


def field_states(field):
    return [str(s) for s in field.get("/_States_", [])]


@app.get("/")
def upload_page():
    return """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>Upload PDF</title>
      <style>
        body {
          font-family: system-ui, sans-serif;
          max-width: 700px;
          margin: 3rem auto;
        }

        input, button {
          margin-top: 1rem;
        }
      </style>
    </head>
    <body>
      <h1>Upload PDF Form</h1>

      <form method="post" action="/upload" enctype="multipart/form-data">
        <input type="file" name="pdf" accept=".pdf" required>
        <br>
        <button type="submit">Upload</button>
      </form>
    </body>
    </html>
    """


@app.post("/upload")
def upload():
    file = request.files.get("pdf")

    if not file:
        return "No PDF uploaded", 400

    temp_dir = tempfile.mkdtemp()
    pdf_path = Path(temp_dir) / file.filename

    file.save(pdf_path)

    upload_id = str(id(pdf_path))
    uploaded_pdfs[upload_id] = pdf_path

    return redirect(url_for("form", upload_id=upload_id))


@app.get("/form/<upload_id>")
def form(upload_id):
    pdf_path = uploaded_pdfs.get(upload_id)

    if not pdf_path or not pdf_path.exists():
        return "PDF not found", 404

    fields = get_fields(pdf_path)

    html = """
    <!doctype html>
    <html>
    <head>
      <meta charset="utf-8">
      <title>PDF Form</title>

      <style>
        body {
          font-family: system-ui, sans-serif;
          max-width: 900px;
          margin: 2rem auto;
        }

        label {
          display: block;
          margin-top: 1rem;
          font-weight: 600;
        }

        input[type="text"] {
          width: 100%;
          padding: .5rem;
        }

        .checkbox-row {
          margin-top: 1rem;
        }

        button {
          margin-top: 2rem;
          padding: .75rem 1rem;
        }
      </style>
    </head>

    <body>
      <h1>PDF Form</h1>

      <form method="post" action="/submit/{{ upload_id }}">
        {% for name, field in fields.items() %}

          {% set ft = field_type(field) %}
          {% set label = field_label(name, field) %}

          {% if ft == "/Btn" %}
            <div class="checkbox-row">
              <label>
                <input
                  type="checkbox"
                  name="{{ name }}"
                  value="__checked__"
                >
                {{ label }}
              </label>
            </div>

          {% else %}
            <label for="{{ name }}">{{ label }}</label>

            <input
              id="{{ name }}"
              name="{{ name }}"
              type="text"
            >
          {% endif %}

        {% endfor %}

        <button type="submit">
          Generate Filled PDF
        </button>
      </form>
    </body>
    </html>
    """

    return render_template_string(
        html,
        upload_id=upload_id,
        fields=fields,
        field_type=field_type,
        field_label=field_label,
    )


@app.post("/submit/<upload_id>")
def submit(upload_id):
    pdf_path = uploaded_pdfs.get(upload_id)

    if not pdf_path or not pdf_path.exists():
        return "PDF not found", 404

    fields = get_fields(pdf_path)
    submitted = request.form.to_dict()

    values = {}

    for name, field in fields.items():
        ft = field_type(field)

        if ft == "/Btn":
            states = field_states(field)

            checked_value = next(
                (s for s in states if s != "/Off"),
                "/Yes",
            )

            if name in submitted:
                values[name] = NameObject(checked_value)
            else:
                values[name] = NameObject("/Off")

        else:
            values[name] = submitted.get(name, "")

    reader = PdfReader(str(pdf_path))
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update({
            NameObject("/AcroForm"):
                reader.trailer["/Root"]["/AcroForm"]
        })

        writer._root_object["/AcroForm"].update({
            NameObject("/NeedAppearances"):
                BooleanObject(True)
        })

    for page in writer.pages:
        writer.update_page_form_field_values(page, values)

    output_path = pdf_path.parent / f"filled_{pdf_path.name}"

    with output_path.open("wb") as f:
        writer.write(f)

    return send_file(
        output_path,
        as_attachment=True,
        download_name=output_path.name,
        mimetype="application/pdf",
    )


if __name__ == "__main__":
    app.run(debug=True)