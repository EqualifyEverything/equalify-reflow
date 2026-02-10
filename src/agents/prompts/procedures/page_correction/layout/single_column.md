# Single-Column Layout

This page uses a standard single-column layout. The spatial position of text in the image matches the linear order of the markdown -- top to bottom.

## Artifact dictionary

- **Paragraph boundary misdetection**: Docling may merge two separate paragraphs into one (missing blank line) or split one paragraph into two (extra blank line). Check whether the image shows a paragraph break where the markdown does.
- **List continuation errors**: A multi-line list item may be split into separate items, or consecutive items may be merged. Verify each list item boundary against the image.
- **Indented block quotes misclassified**: Indented text (like block quotes or callout boxes) may be extracted as regular paragraphs or as code blocks. Check formatting against the image.
