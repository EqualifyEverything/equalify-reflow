# Pages with Equations

This page contains mathematical equations (display or complex inline).

## Verification checklist

- **Equation text**: Compare equation text in the markdown against the image where possible. Simple inline equations like *x = y + z* should match.
- **Display vs inline**: Display equations (centered, on their own line) should not be merged into surrounding paragraphs.
- **Equation numbers**: If equations are numbered (e.g., "(1)", "(2)"), verify the numbers match the image.

## Artifact dictionary

- **Garbled LaTeX**: Docling may extract equations as garbled text mixing LaTeX commands with rendered characters. Note severe cases in reasoning but fix what you can.
- **Missing subscript/superscript**: Subscripts and superscripts in equations may be extracted as regular text (e.g., "x2" instead of "x^2", "ai" instead of "a_i").
- **Equation numbers misplaced**: Equation numbers like "(1)" may be extracted on a different line or merged into the equation text.
- **Greek letters misrecognized**: Greek letters may be OCR'd as similar Latin letters (e.g., rho as "p", mu as "u", sigma as "o").
