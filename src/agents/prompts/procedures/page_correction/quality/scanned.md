# Scanned Document Quality

This page appears to be a scan of a physical document. Expect lower baseline extraction quality. Focus on the highest-impact OCR errors rather than trying to fix everything.

## Scan-specific guidance

- OCR quality varies by scan resolution and document condition. Some errors may not be fixable from the image alone.
- If a word is illegible in the image, do not guess -- leave the OCR result as-is and note the uncertainty.
- Focus on errors that change meaning rather than minor character-level issues.

## Artifact dictionary

- **Character confusion**: Common OCR substitutions: `l`/`1`/`I`, `O`/`0`, `rn`/`m`, `cl`/`d`, `vv`/`w`. Check suspicious words against image context.
- **Speckle noise creating phantom characters**: Scan noise may be interpreted as periods, commas, or stray letters. If the image shows clean text but the markdown has extra punctuation, remove it.
- **Skew causing line merges**: If the scan is slightly rotated, adjacent lines may be merged into one. Check that line breaks match the image.
- **Faded text gaps**: Light or faded text may be missing from the extraction entirely. If you can read text in the image that is absent from the markdown, add it.
