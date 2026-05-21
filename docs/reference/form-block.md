# The `form` block

When the pipeline finds a fillable form on a page, it does not emit raw inline
HTML. Instead the `page_content` form subagent (see
[pipeline-phases.md](pipeline-phases.md)) replaces the flattened form text with
a single self-contained fenced **`form` block** — a small, composable DSL that
describes the whole form. A downstream renderer (the viewer, a WordPress
plugin, an Obsidian-style code-block processor) compiles the block into
accessible form controls.

This keeps the converted markdown clean and round-trippable: one block per form,
trivially diffable, and validated by a deterministic renderer rather than
trusting an LLM to emit correct HTML. The pattern is modelled on Obsidian custom
code-block processors (e.g. Meta Bind input fields).

> **Accessibility note:** the `form` block is an *intermediate representation*.
> It is only accessible once a renderer turns it into labelled controls. Any
> surface that ships the converted document to end users must run a `form`-block
> renderer; raw, unrendered blocks are not accessible on their own.

## Grammar

````
```form
legend: <form title>            # optional, at most one, must be first
- <type>[*] | <label>           # one field per line, in document order
- <type>[*] | <label> | <opt>; <opt>; <opt>
```
````

- **`legend:`** — optional form title, used as the rendered `<fieldset>`
  `<legend>` / form heading. Omitted if the form has no overall title.
- **Field lines** start with `- ` and have up to three `|`-separated columns:
  1. `<type>` with an optional trailing `*` marking a **required** field.
  2. `<label>` — the field's accessible label (the group prompt for grouped
     types).
  3. `<options>` — for `radio` / `multiselect` / `select` only:
     semicolon-separated choice labels, in display order.
- Fields appear in top-to-bottom page order.

### Field types

| Token | Control | Notes |
|---|---|---|
| `text` | single-line text input | name, email, short answer |
| `textarea` | multi-line text input | comments, long answers |
| `checkbox` | single checkbox | one yes/no statement; no options column |
| `multiselect` | checkbox group | "select all that apply"; uses options |
| `radio` | radio-button group | exactly one choice; uses options |
| `select` | dropdown | uses options |
| `date` | date input | |
| `signature` | signature line | rendered as a labelled text field |

### Escaping

Labels and option text are sanitised by the renderer before they enter a block:
newlines collapse to spaces, a literal `|` becomes `/`, and a literal `;`
becomes `,`. Blocks therefore never contain a raw delimiter inside a value, and
parsers can split on `|` and `;` without quoting.

## Example

A registration form flattened by Docling as:

```
Course Registration
Full name: ____________________
Date of birth: __/__/____
Standing: ☐ Undergraduate ☐ Graduate
Topics (choose any): ☐ AI ☐ Systems ☐ Theory
☐ I confirm the information is accurate *
```

is rebuilt as:

````
```form
legend: Course Registration
- text* | Full name
- date | Date of birth
- radio* | Standing | Undergraduate; Graduate
- multiselect | Topics (choose any) | AI; Systems; Theory
- checkbox* | I confirm the information is accurate
```
````

## Reference renderer

A compliant renderer should produce, per field, a programmatically associated
label and control:

- `text` / `textarea` / `date` / `signature`: a `<label for>` bound to the
  control by `id`.
- `checkbox`: the control followed by its `<label for>`.
- `radio` / `multiselect` / `select`: a `<fieldset>` with the label as
  `<legend>`, one control per option (radios share a `name`).
- Required fields carry `aria-required="true"` and a visible required marker.

The pipeline guarantees a well-formed block; renderers should still skip
unknown type tokens gracefully (render as `text`) so the format can grow.
