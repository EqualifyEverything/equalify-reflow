# Double-Column Layout

This page uses a two-column layout. Docling has linearized the columns into reading order -- left column first, then right column. This means:

- The spatial position of text in the image will NOT match the linear order of the markdown. A paragraph in the top-right of the image may be halfway through the markdown, after all left-column content.
- Read the image **column by column** (left top-to-bottom, then right top-to-bottom) to follow the same order as the markdown.
- Figures and tables that span both columns may appear at a different position in the markdown than where they sit visually on the page. This is expected -- do not try to reorder them.

## Artifact dictionary

- **Column bleed**: Text from the end of the left column merged with text from the start of the right column, creating a nonsensical sentence that splices two unrelated passages. If you see a sentence that abruptly changes topic mid-flow, check whether it corresponds to a column boundary in the image.
- **Misattributed text**: Captions, headers, or footnotes that sit between columns may have been placed in the wrong column's text stream. Verify that each paragraph's content matches what appears in its column on the image.
- **Duplicated text**: Docling occasionally extracts the same text from both the column and a spanning element (like a header), resulting in duplication. If you see the same phrase twice and the image only shows it once, remove the duplicate.
