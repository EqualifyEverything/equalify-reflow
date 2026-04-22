# Institute for Public Health Summer Research Program

## Document Description
A 4-page brochure from Washington University in St. Louis Institute for Public Health advertising their Summer Research Program with three tracks: Aging & Neurological Diseases, Public & Global Health, and Cardiovascular Disease & Hematology (RADIANCE). Page 1 is an overview; pages 2-4 are dedicated to each track with eligibility, benefits, and application details.

## Document Characteristics
- Page count: 4
- Content type: Mixed layout brochure with repeated structure per track
- Notable features: Color-coded sections (green for Aging, orange for Public Health, purple for Cardiovascular), group photos per track, icon-based feature highlights (people icons, microscope icons, presentation icons), repeated Washington University logo on each page, nested bullet lists in eligibility sections

## What the Conversion Did Well
- Extracted all body text accurately across all four pages
- Preserved heading hierarchy for track names and sub-sections (Work With Top Investigators, Build a Network, etc.)
- Eligibility bullet lists correctly captured with nested sub-bullets on page 4 (RADIANCE track)
- Contact emails for each track captured (centerforaging@wustl.edu, iphsummer@wustl.edu, radiance@wustl.edu)
- Application periods and program dates captured for each track
- The RADIANCE acronym styling preserved (italic "new")
- Figure-5 (group photo of students in purple t-shirts) has a good descriptive alt text
- Figure-8 (networking icon) has a reasonable alt text
- Figure-10 (seminar audience photo) has a good descriptive alt text
- Figure-13 (presentation icon) has a good alt text
- Grant/funding support statements captured for each track
- The footnote about underrepresented groups on page 4 was captured including the bit.ly link

## What the Conversion Could Improve
- On page 1, the text "AGING & NEUROLOGICAL DISEASES" appears twice — once as an h2 heading (line 5) and again as plain text (line 9), creating redundancy
- Similarly "RADIANCE" appears as orphaned plain text (line 19) separate from the Cardiovascular heading
- Figure-17 appears twice in the markdown (lines 140 and 162) — a duplicate reference
- The three-column icon layout on each track page (e.g., "Work With Top Investigators" / "Build a Network" / "Gain Real-World Experience") loses its visual parallel structure in the markdown — text fragments are split around inline images, making them read awkwardly (e.g., "Work in research labs and [image] centers focused on aging...")
- The repeated "Institute for Public Health Summer Research Program" header that appears at the top of pages 2-4 is not captured in the markdown — the track-specific content just flows continuously
- The "SHARE WITH STUDENTS" context from the document title/filename is not reflected anywhere in the conversion
- The stipend/pay information on page 2 reads "Accepted students receive pay. Non-WashU students Metrolink transit pass." — missing "receive a" before "Metrolink" (matches the PDF text, so this is a source document issue rather than conversion error)

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| "AGING & NEUROLOGICAL DISEASES" appears twice on page 1 (heading and plain text) | Major | Structure |
| "RADIANCE" appears as orphaned plain text separate from Cardiovascular heading | Major | Structure |
| Figure-17 appears twice in the markdown -- duplicate reference | Major | Figures/Images |
| Three-column icon layout loses visual parallel structure -- text split around inline images | Major | Structure |
| Repeated "Institute for Public Health Summer Research Program" header on pages 2-4 not captured | Major | Content Accuracy |
| "SHARE WITH STUDENTS" context from document title not reflected in conversion | Minor | Content Accuracy |

**Total: 6 issues (0 critical, 5 major, 1 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 1 minutes 50 seconds |
| Conversion Cost | $0.38 |
| Token Usage | 307,717 tokens |
| Total Pages | 4 |
| Total Edits | 27 |
