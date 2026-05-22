# Pages with Forms

This page contains a form — fields a person is expected to fill in (labelled
blanks, checkboxes, option groups, dropdowns, date blanks, signature lines).
Docling renders these as lossy plain text: a label followed by underscores, a
stray `☐` or `[ ]`, options run together.

## Leave the form fields alone

A dedicated later step converts form fields into accessible HTML by reading the
page image. Do **not** try to fix the form yourself:

- Do not delete, shorten, or "tidy" runs of underscores (`____`) — they mark
  where a field goes and are used to locate it.
- Do not remove or rewrite checkbox glyphs (`☐`, `[ ]`) or the option text
  next to them.
- Do not merge a field's label into the blank or restructure the layout.

Apply your normal corrections to the surrounding prose (OCR fixes, headings,
lists), but treat the form fields themselves as read-only. Touching them can
prevent the form step from locating and converting them.
