# Pages with Forms

This page contains a form — fields a person is expected to fill in (labelled
blanks, checkboxes, option groups, dropdowns, date blanks, signature lines).

Docling renders forms as lossy plain text: a label followed by underscores, a
stray `☐` or `[ ]`, option words run together, the field grouping lost
entirely. That text is not accessible — a screen-reader user cannot tell it is
a form, what the fields are, or which options belong together.

## What to do

Identify each contiguous form region in the page markdown, then call
`reconstruct_form` with that text. The tool re-reads the form from the page
image and returns a structured `form` block. Use `str_replace` to replace the
original lossy form text with the returned block.

Replace the whole region in one edit — do not leave half the form as raw
underscores and half as a `form` block.

## Verification checklist

- **Field boundaries**: Each label + its blank is one field; don't merge two.
- **Control type**: A single box is a `checkbox`; a "pick one" group is `radio`;
  a "select all" group is `multiselect`; a chevroned box is `select`.
- **Options**: Every option label in a group is captured, in order.
- **Required**: Asterisks / "required" markers are preserved.

## Tool

- **`reconstruct_form(form_text, reasoning)`**: Pass the exact markdown text of
  the form region and why it needs rebuilding. Returns a `form` block to apply
  with `str_replace`.

## Tool call example

<example>
<description>Rebuilding a registration form Docling flattened</description>
<tool_call>
reconstruct_form(
  form_text="Name: ____________\nStatus: ☐ Single ☐ Married\n☐ I agree to the terms",
  reasoning="Flattened form: a text field, a single-choice group, and a consent checkbox"
)
</tool_call>
<follow_up>
After receiving the form block, use str_replace:
str_replace(
  old_text="Name: ____________\nStatus: ☐ Single ☐ Married\n☐ I agree to the terms",
  new_text="[form block from tool output]",
  reasoning="Replaced flattened form text with structured accessible form block",
  category="form_fix"
)
</follow_up>
</example>
