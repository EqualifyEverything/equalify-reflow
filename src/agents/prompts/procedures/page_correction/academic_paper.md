# Procedure: Academic Paper Page Correction

You are correcting one page of a two-column academic paper. The page image is your ground truth. The markdown is Docling's extraction of that page. Your job is to make the markdown match what the image shows.

## Understanding the layout

Academic papers are typically set in two columns. Docling has already linearized the columns into reading order — left column first, then right column. This means:

- The spatial position of text in the image will NOT match the linear order of the markdown. A paragraph that appears in the top-right of the image may be halfway through the markdown, after all left-column content.
- Read the image **column by column** (left top-to-bottom, then right top-to-bottom) to follow the same order as the markdown.
- Figures and tables that span both columns may appear at a different position in the markdown than where they sit visually on the page. This is expected — do not try to reorder them.

### Common two-column artifacts to watch for

- **Column bleed**: Text from the end of the left column may have been merged with text from the start of the right column, creating a nonsensical sentence that splices two unrelated passages. If you see a sentence that abruptly changes topic mid-flow, check whether it corresponds to a column boundary in the image.
- **Misattributed text**: Captions, headers, or footnotes that sit between columns may have been placed in the wrong column's text stream. Verify that each paragraph's content matches what appears in its column on the image.
- **Duplicated text**: Docling occasionally extracts the same text from both the column and a spanning element (like a header), resulting in duplication. If you see the same phrase twice and the image only shows it once, remove the duplicate.

## Formatting conventions

Academic papers use inline formatting consistently. Docling frequently misses these. Compare carefully against the image:

### Italic (`*text*`)

Academic papers use italic for:
- **Emphasis** on key terms, typically when a term is first introduced or defined ("We call this the *eigenvalue decomposition*")
- **Latin phrases**: *et al.*, *in vivo*, *in vitro*, *a priori*, *a posteriori*, *ad hoc*, *i.e.*, *e.g.*, *cf.*, *viz.*, *inter alia*
- **Variables and mathematical symbols** when appearing in prose: "where *n* is the number of samples and *k* is the cluster count"
- **Journal and book titles** in reference lists and in-text mentions
- **Gene names, species names** in biological papers: *E. coli*, *Drosophila*, *BRCA1*
- **Foreign words** not commonly adopted into English

When checking italic: look at the image closely. Italic text has a slight rightward slant and often appears thinner than regular weight. In small fonts this can be subtle. If you cannot confidently distinguish italic from regular weight at the image resolution, note this in your reasoning but do not add formatting you are unsure about.

### Bold (`**text**`)

Bold is used sparingly in academic papers:
- **Theorem/definition/proposition labels**: "**Theorem 3.1.**" or "**Definition 2.**"
- **Key results or claims** in some paper styles
- **Column headers in tables** (though table formatting is handled separately)

Bold is visually obvious — thicker stroke weight. If the image shows it, the markdown should have it.

### Monospace (`` `text` ``)

Monospace/code formatting is used for:
- **Software and tool names**: `Python`, `TensorFlow`, `scikit-learn`, `MATLAB`
- **Function and method names**: `fit()`, `predict()`, `main()`
- **File paths and URLs**: `/usr/local/bin`, `https://...`
- **Code snippets inline**: "set `n_clusters=5`"
- **Command-line arguments and flags**: `--verbose`, `-O2`
- **Variable names in code context** (distinct from mathematical variables, which use italic)

In the image, monospace text typically appears in a fixed-width font (Courier-like) that looks distinctly different from the proportional serif body text. This is usually easy to identify.

### Superscript and subscript

- **Footnote markers** are superscript numbers in the body text. Docling may render these as regular-sized numbers. Verify they are present but do not change their formatting — footnote handling is a later phase.
- **Mathematical superscripts/subscripts**: x², H₂O. If Docling captured these as plain text (x2, H2O), correct them if the markdown supports it and the image clearly shows super/subscript.

## Footnotes and citations

### Footnotes

Footnotes in academic papers appear as:
1. **A superscript number in the body text** (the marker): "...recent work¹ has shown..."
2. **A footnote body at the bottom of the page**, below a horizontal rule or separator, starting with the matching number

**Your job**: Verify that footnote markers exist in the markdown where the image shows them. Verify that footnote body text at the bottom of the page is present and accurate. Do NOT relocate footnotes or change their numbering — a later phase handles footnote relocation.

If Docling rendered the footnote marker as a regular number (e.g., "recent work1 has shown" instead of "recent work¹ has shown"), leave it. The structure phase has already cataloged footnotes.

### Citations

Academic citations appear in several styles. The most common:

- **Numbered**: `[1]`, `[2, 3]`, `[1-5]`, `[14, 15, 23]`
- **Author-year parenthetical**: `(Smith, 2024)`, `(Smith & Jones, 2024)`, `(Smith et al., 2024)`
- **Author-year narrative**: `Smith (2024) showed...`, `Smith and Jones (2024) found...`
- **Numbered superscript**: Similar to footnotes but referencing the bibliography

Verify that citation markers in the markdown match those visible in the image. Common Docling errors:
- Missing brackets: `1` instead of `[1]`
- Merged citations: `[1,2,3]` when the image shows `[1, 2, 3]` with spaces
- Split citations: a citation that spans a line break in the image may be extracted as two separate bracket groups
- Author name errors in author-year styles: OCR can mangle names

## Common elements on academic paper pages

### Abstract (usually first page only)

Abstracts are typically indented or in a slightly different font size. Docling may or may not capture the "Abstract" label. Verify the abstract text matches the image. The abstract often uses a slightly different margin — this doesn't affect the markdown.

### Section headers with numbering

Academic papers use numbered sections: "1 Introduction", "2.1 Related Work", "3.2.1 Experimental Setup". Verify the numbering matches the image. Do NOT change heading levels (# vs ## vs ###) — that was fixed in the structure phase. Only verify the text content matches.

### Equations

Display equations (centered, on their own line) may be extracted poorly by Docling. If the image shows a clean equation and the markdown has garbled text, note this in your reasoning. Simple inline equations (like *x = y + z*) should be verified against the image. Complex LaTeX-style equations may not render correctly in markdown — do what you can but note limitations.

### First-page artifacts

The first page of an academic paper often contains:
- Paper title (verify text accuracy)
- Author names and affiliations (verify text, check for superscript affiliation markers)
- Conference/journal header or footer (DOI, copyright notice, page numbers)
- These headers/footers may appear in the markdown — verify accuracy but don't remove them (the structure phase handles what belongs and what doesn't)

### References section

If your page contains part of the references/bibliography section:
- Reference entries are dense and error-prone. Check carefully for author names, years, journal titles (should be italic), volume numbers.
- Numbered references should maintain their numbering.
- URLs and DOIs in references are common — verify accuracy.

## Priority order

When reviewing a page, work through these in order:

1. **Text accuracy**: Does every word in the markdown match the image? OCR errors are the highest-impact issue.
2. **Structural accuracy**: Are lists, paragraphs, and block elements correctly structured?
3. **Inline formatting**: Are italic, bold, and monospace applied where the image shows them?
4. **Citation and reference accuracy**: Do citation markers match the image?
5. **Minor formatting**: Spacing, punctuation, special characters.

If the page is clean and matches well, use `no_changes`. Not every page needs corrections. A well-extracted page with no issues is a valid outcome.
