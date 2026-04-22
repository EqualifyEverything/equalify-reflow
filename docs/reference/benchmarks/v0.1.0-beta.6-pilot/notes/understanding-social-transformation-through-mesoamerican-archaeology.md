# Understanding Social Transformation Through Mesoamerican Archaeology

## Document Description
An event poster for a "Zona Abierta" lecture by Dr. Rodrigo Liendo Stuardo on Mesoamerican archaeology, presented by the UIC Latino Cultural Center, Department of Anthropology, and UNAM. The event was held Thursday, October 20, 2016, at the Latino Cultural Center, Lecture Center B2.

## Document Characteristics
- Page count: 1
- Content type: Mixed layout event poster
- Notable features: Grid of archaeological artifact photos at top, large decorative title typography, speaker headshot, multiple organizational logos (UNAM Chicago, Zona Abierta, UIC), social media handles in icon-based footer, bold color scheme (purple, yellow, green)

## What the Conversion Did Well
- Extracted the main body text (abstract and speaker bio) accurately
- Captured the event date, time, and location details
- Preserved the bold formatting on organization names and speaker name
- Captured the co-sponsor information correctly
- Included the contact information and accessibility statement
- Figure-3 (Zona Abierta logo) has a good alt text description
- Figure-5 (Dr. Liendo Stuardo headshot) has a reasonable alt text

## What the Conversion Could Improve
- The main title "Understanding Social Transformation Through Mesoamerican Archaeology" is completely missing from the markdown text — it only appears embedded in figure-1 which has no alt text. This is the most important text on the poster.
- "Zona Abierta" series name is missing from the text (only in figure-1)
- Figure-1 (the title banner with archaeological photo grid) has empty alt text — since the main title is completely missing from the markdown text and only exists in this image, the missing alt text means the document's title is entirely inaccessible
- Figures 7-9 are the social media handles (Facebook /UICLCC, Twitter @UICLatinoCenter, Instagram @UICLCC) — all have empty alt text. These handles do not appear anywhere in the markdown text, so this contact information is completely lost. These should be rendered as text links since they contain actionable contact information.
- The "Rafael Cintron Ortiz Latino Cultural Center" and "Department of Anthropology" text from the logo area at the bottom is not captured
- There is a duplicate "### WHERE:" heading on line 28 that appears to be an extraction error
- The reading order is somewhat jumbled — the WHEN/TIME/WHERE details are separated from each other, and the speaker bio appears after a string of undescribed images rather than in a natural flow
- The heading hierarchy uses h3 (###) for WHEN, TIME, WHERE, and "FREE refreshments" which is not semantically ideal for an event poster — these are more like labeled fields than subsections

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Main title "Understanding Social Transformation Through Mesoamerican Archaeology" completely missing from markdown text | Critical | Content Accuracy |
| "Zona Abierta" series name missing from text (only in figure-1) | Critical | Content Accuracy |
| Figure-1 (title banner) has empty alt text — title only exists in this image, completely inaccessible | Major | Accessibility |
| Figures 7-9 (social media handles: Facebook, Twitter, Instagram) have empty alt text; handles not in text, contact info lost | Major | Figures/Images |
| "Rafael Cintron Ortiz Latino Cultural Center" and "Department of Anthropology" text not captured from logo area | Major | Content Accuracy |
| Duplicate "### WHERE:" heading (extraction error) | Major | Structure |
| Reading order jumbled — WHEN/TIME/WHERE details separated, speaker bio after undescribed images | Major | Structure |
| Heading hierarchy uses h3 for labeled fields (WHEN, TIME, WHERE) which is not semantically ideal | Minor | Structure |

**Total: 8 issues (2 critical, 4 major, 2 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 0 minutes 53 seconds |
| Conversion Cost | $0.08 |
| Token Usage | 60,944 tokens |
| Total Pages | 1 |
| Total Edits | 7 |
