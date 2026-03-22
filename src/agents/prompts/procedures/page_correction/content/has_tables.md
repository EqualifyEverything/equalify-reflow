# Pages with Tables

This page contains tabular data (rows and columns).

## Verification checklist

- **Table structure**: Verify the markdown table has the correct number of rows and columns matching the image.
- **Header row**: Check that the header row content is accurate and properly separated with pipe characters.
- **Cell content**: Spot-check individual cell values, especially numeric data which is OCR-prone.
- **Merged cells**: Check if the image shows cells spanning multiple rows or columns that the markdown cannot represent.

## Artifact dictionary

- **Column misalignment**: Markdown table columns may not align with the image -- cell content may be shifted left or right by one column.
- **Merged cells extracted as separate rows**: Cells that span multiple rows or columns in the image may be extracted as separate single cells, duplicating content or creating empty cells.
- **Header row missing pipe separators**: The header separator row (`|---|---|`) may be malformed or missing entirely.
- **Numeric data OCR errors**: Numbers in table cells are error-prone: `1` vs `l`, `0` vs `O`, decimal points missing or misplaced.
- **Orphaned total/summary rows**: A line like `Total ... 550 pts` appearing outside a table (often as a heading or plain text) may be an escaped totals row. If the image shows it as part of the table, include it when reconstructing.
- **Table continues from previous page**: If the table appears to start mid-data with no header row, it likely continues from the previous page. Reconstruct this page's portion faithfully — a later cross-page step will merge the halves. Note the continuation in your reasoning.

## Tools

- **`reconstruct_table(ref_id)`**: Call this for tables with structural issues (wrong column count, misaligned cells, missing rows, or merged cells visible in the image that the markdown table cannot represent). The tool will re-read the table from the page image and return a corrected version. Use `str_replace` to replace the old table with the reconstructed output.

## Tool call example

<example>
<description>Reconstructing a table with misaligned columns</description>
<tool_call>
reconstruct_table(ref_id="table-1")
</tool_call>
<follow_up>
After receiving the reconstructed table, use str_replace:
str_replace(
  old_text="| Header 1 | Header 2 |\n|---|---|\n| misaligned | data |",
  new_text="[reconstructed table from tool output]",
  reasoning="Table had column misalignment; replaced with vision-reconstructed version",
  category="table_fix"
)
</follow_up>
</example>
