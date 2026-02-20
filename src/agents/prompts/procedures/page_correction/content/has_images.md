# Pages with Images

This page contains photographs, diagrams, figures, or illustrations.

## Verification checklist

- **Figure captions**: Verify caption text in the markdown matches the caption visible in the image. Captions are high-value text -- errors here are especially impactful.
- **Figure reference numbering**: If the text refers to "Figure 3", verify the numbering matches what the image shows.
- **Alt text placeholders**: Docling may insert `<!-- image -->` placeholders. Leave these as-is -- a later step handles image inlining.

## Alt text generation

You have access to a `describe_image` tool. For each figure reference in this page's markdown (e.g. `![](figures/figure-1.png)`), call `describe_image` with the figure's `ref_id` (e.g. `figure-1.png`).

The tool returns an instruction with the alt text to use. Then use `str_replace` to update the markdown image syntax from `![](figures/figure-1.png)` to `![Generated alt text](figures/figure-1.png)`.

- Call `describe_image` once per figure. Do not skip any figures.
- If the tool returns a decorative result, use an empty alt: `![](figures/figure-1.png)` (no change needed).
- If the tool returns an error for a ref_id, flag it in `no_changes` notes and move on.

## Artifact dictionary

- **Caption text merged with body text**: A figure caption may be extracted as part of the preceding or following paragraph rather than as its own block. If the image shows a distinct caption below a figure, it should be on its own line in the markdown.
- **Figure labels misread**: "Figure" abbreviated to "Fig." or OCR errors in figure numbers (e.g., "Hgure" instead of "Figure").
- **Caption split across lines**: Multi-line captions may be broken into separate paragraphs. Verify the full caption is contiguous.
