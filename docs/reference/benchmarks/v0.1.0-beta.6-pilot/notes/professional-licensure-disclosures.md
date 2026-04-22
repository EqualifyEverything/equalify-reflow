# Professional License-Related Disclosures: New Federal Regulations

## Document Description
An 8-slide presentation from the University of Illinois System dated March 9, 2020, covering new federal regulations (34 CFR 668.43) regarding professional license-related disclosures for educational programs. Topics include applicability, general and specific disclosures, student location determination, NC-SARA distance learning requirements, and other affected disclosures.

## Document Characteristics
- Page count: 8 (presentation slides)
- Content type: Text-heavy presentation with bullet points and nested lists
- Notable features: Consistent slide template with "University of Illinois System" header banner and footer logos, "Inspire integrity by speaking up" badge on each slide, underlined text for emphasis on key terms, source citations (CFR references) on each slide, hierarchical bullet points using arrows and circles

## What the Conversion Did Well
- All body text across all 8 slides is accurately extracted
- Nested bullet list structure is well-preserved (top-level bullets, sub-bullets with arrows, third-level with circles)
- Source citations (34 CFR references) correctly captured on each slide
- Underlined text preserved using HTML `<u>` tags (e.g., "educational requirements" on slide 2)
- Italic formatting for "meets" and "does not meet" on the General Disclosures slide preserved correctly
- Bold formatting for "affected students", "prior to the student's enrollment", "does not meet", "within 14 calendar days" preserved on slide 4
- The "Other Disclosures" list on slide 7 with all 10 items and their (amended)/(new) annotations captured accurately
- Heading hierarchy (h2 for slide titles) is consistent and appropriate
- The NC-SARA section captured the ellipsis ". . . ." in the original text

## What the Conversion Could Improve
- Figure-1 (the "Altogether Extraordinary" header banner with University of Illinois System branding and composite imagery of Chicago skyline, Abraham Lincoln statue, and Alma Mater statue) has empty alt text
- Figure-2 and figures 5, 8, 10 (University of Illinois System three-campus logo icons repeated on each slide footer) all have empty alt text — these are decorative/repeated branding elements that could be marked as decorative or described once
- Figures 6, 7, 9, 11 (the green "Inspire integrity by speaking up" speech bubble badge) all have empty alt text — also a repeated decorative element
- Figure-14 (large question mark icon on the Questions slide) has empty alt text — decorative but should be described
- Figure-15 (University of Illinois System full logo on final slide) has empty alt text
- Lines 23-25 contain two empty image references `![]()` with no figure file paths at all — these appear to be extraction artifacts from slides 2-3 where no actual content images exist (just the repeated footer/badge elements)
- Line 78 has a broken image reference `![](figures/figure-11)` missing the `.png` extension
- The slide-by-slide structure is flattened into one continuous document — there are no page breaks or slide separators, making it unclear where one slide ends and the next begins
- The "UNIVERSITY OF ILLINOIS SYSTEM" text that appears in the footer of every slide is not captured as text
- The "Altogether Extraordinary" tagline from the title slide banner is not captured
- On slide 4 (Specific Disclosures), the original has "Prospective students" and "Currently enrolled students" as underlined sub-headers with arrow markers, but in the markdown they appear as plain indented list items without the underline emphasis that distinguishes them as category labels

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Figure-1 (header banner) has empty alt text | Minor | Accessibility |
| Figures 2, 5, 8, 10 (campus logo icons) have empty alt text | Minor | Accessibility |
| Figures 6, 7, 9, 11 (integrity badge) have empty alt text | Minor | Accessibility |
| Figure-14 (question mark icon) has empty alt text | Minor | Accessibility |
| Figure-15 (full logo on final slide) has empty alt text | Minor | Accessibility |
| Two empty image references `![]()` with no figure file paths (extraction artifacts) | Major | Figures/Images |
| Broken image reference `![](figures/figure-11)` missing .png extension | Major | Figures/Images |
| Slide-by-slide structure flattened with no slide separators | Major | Structure |
| "UNIVERSITY OF ILLINOIS SYSTEM" footer text not captured | Minor | Content Accuracy |
| "Altogether Extraordinary" tagline not captured | Minor | Content Accuracy |
| Underlined sub-headers on slide 4 rendered as plain list items | Minor | Formatting |

**Total: 11 issues (0 critical, 3 major, 8 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 1 minute 41 seconds |
| Conversion Cost | $0.38 |
| Token Usage | 321,407 tokens |
| Total Pages | 8 |
| Total Edits | 17 |
