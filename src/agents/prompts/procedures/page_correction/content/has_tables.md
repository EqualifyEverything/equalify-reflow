# Pages with Tables

This page contains tabular data (rows and columns).

## Verification checklist

- **Table structure**: Verify the markdown table has the correct number of rows and columns matching the image.
- **Header row**: Check that the header row content is accurate and properly separated with pipe characters.
- **Cell content**: Spot-check individual cell values, especially numeric data which is OCR-prone.

## Artifact dictionary

- **Column misalignment**: Markdown table columns may not align with the image -- cell content may be shifted left or right by one column.
- **Merged cells extracted as separate rows**: Cells that span multiple rows or columns in the image may be extracted as separate single cells, duplicating content or creating empty cells.
- **Header row missing pipe separators**: The header separator row (`|---|---|`) may be malformed or missing entirely.
- **Numeric data OCR errors**: Numbers in table cells are error-prone: `1` vs `l`, `0` vs `O`, decimal points missing or misplaced.
