# Pages with Lists

This page contains list content (bulleted, numbered, or definition lists).

## Verification checklist

- **Item boundaries**: Verify each list item in the markdown corresponds to exactly one item visible in the image. Multi-line items should not be split into separate items.
- **Nesting depth**: Check that nested lists match the indentation visible in the image. Docling may flatten nested lists or introduce false nesting.
- **List type**: Verify the correct list type — unordered (bullets) vs ordered (numbers) — matches the image.
- **Definition lists**: Check if the image shows term/definition pairs (bold term followed by description). These cannot be represented in markdown and need `reconstruct_list`.

## Artifact dictionary

- **Merged items**: Two separate list items concatenated into a single item, losing the boundary between them.
- **Split items**: A single multi-line list item broken into multiple separate items.
- **Flattened nesting**: A nested (indented) sub-list rendered at the same level as the parent list.
- **Mixed list types**: Numbered items mixed with bullets when the image shows a uniform list type.
- **Definition list as paragraphs**: Term/definition pairs rendered as plain bold text and paragraphs instead of a structured list.
- **Bullet character inconsistency**: Different bullet characters (-, *, +) used inconsistently within the same list.

## Tools

- **`reconstruct_list(list_text, reasoning)`**: Call this for lists with structural issues (merged/split items, wrong nesting, or definition lists that need HTML `<dl>` markup). Pass the problematic list text and explain the issue. The tool will re-read the list from the page image and return a corrected version. Use `str_replace` to apply the result.

## Tool call example

<example>
<description>Reconstructing a list with merged items</description>
<tool_call>
reconstruct_list(
  list_text="- First item Second item\n- Third item",
  reasoning="Image shows three separate bullet points but items 1 and 2 are merged in the markdown"
)
</tool_call>
<follow_up>
After receiving the corrected list, use str_replace:
str_replace(
  old_text="- First item Second item\n- Third item",
  new_text="- First item\n- Second item\n- Third item",
  reasoning="Applied vision-reconstructed list structure",
  category="list_fix"
)
</follow_up>
</example>
