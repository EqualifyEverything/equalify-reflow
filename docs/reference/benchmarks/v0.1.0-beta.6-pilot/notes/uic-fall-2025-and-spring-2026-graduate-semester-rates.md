# UIC Fall 2025 and Spring 2026 Graduate Semester Rates

## Document Description
A UIC tuition and fees schedule for graduate students, containing four main rate tables (tuition, tuition differentials by college/program, fees, and assessments), followed by detailed footnotes covering policies on refunds, international student surcharges, regional site nuances, and guaranteed tuition plans.

## Document Characteristics
- Page count: 3
- Content type: Table-heavy with footnotes
- Notable features: 4 data tables with financial figures, per-credit-hour rates section, numbered notes (1-14) with sub-items (a-h), hyperlinks (Refund Schedule, CTA U-Pass URL, email), merged cells in tables, color-coded header rows (dark blue)

## What the Conversion Did Well
- All four tables (Tuition, Tuition Differential, Fees, Assessments) are converted to markdown tables with accurate data
- Dollar amounts across all tables appear correct and complete
- Table 2 (Tuition Differential) correctly captures all college/program combinations with their respective range rates
- Table 3 (Fees) accurately lists all fee types with correct amounts across all four ranges
- The per-credit-hour tuition rates for Law School, Health Professions Education, and Master of Health Professions Education are captured
- Numbered notes (1-14) are fully transcribed with accurate content
- Sub-items under note 11 (a through h) with their respective surcharge amounts are preserved
- The footnote reference for DNP (Nursing) is converted to markdown footnote syntax and resolved at the end
- The Refunds section with its 3 items is included
- Heading hierarchy is reasonable (H1 for title, H2 for each table and section)

## What the Conversion Could Improve
- Table 1 (Graduate Tuition per semester) structure is imperfect: the PDF shows a nested header with Range I-IV labels spanning sub-columns for In-State and Out-of-State, but the markdown table has empty cells in the header row and the multi-level header structure is flattened. The In-State/Out-of-State distinction is present but the visual nesting is lost.
- The "Assessments Subtotal" row ($623, $416, $207, $104) visible at the top of page 2 in the PDF is missing from the markdown output. This is a data loss.
- The per-credit-hour tuition rate section (lines 44-52) is rendered as plain text paragraphs rather than in any structured format. In the PDF, these are presented in a tabular layout with clear In-State/Out-of-State columns. The markdown loses this structure entirely.
- The "Estimated Cost of Attendance" text on page 1 appears as a hyperlink in the PDF (underlined, blue text) but is rendered as plain text in the markdown with no link.
- The CTA U-Pass note URL (https://idcenter.uic.edu/cta-u-pass/) is present but the asterisk/note formatting differs from the PDF which shows it as a footnote-style note below the table.
- The "No CTA U-Pass for 5 hours or less" merged cell in Table 3 is handled but the empty cell for Range IV in the CTA U-Pass row and the merged cell spanning Ranges III-IV for U-Pass+ are not perfectly represented.
- The Refund Schedule links in the Refunds section point to "(Refund Schedule)" as text rather than actual URLs -- the PDF shows these as clickable hyperlinks.
- The email address orhelp@uic.edu in note 2 is present in text but not formatted as a mailto link as it appears in the PDF.
- The horizontal rule separator (---) on line 112 before Endnotes does not correspond to any visual element in the PDF and seems arbitrary.
- Note 4 contains an escaped underscore in the URL (guaranteed\_tuition) which is a markdown artifact.

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Table 1 nested header structure flattened (Range I-IV spanning sub-columns lost) | Major | Structure |
| "Assessments Subtotal" row ($623, $416, $207, $104) missing from output | Critical | Content Accuracy |
| Per-credit-hour tuition rates rendered as plain text instead of tabular format | Major | Structure |
| "Estimated Cost of Attendance" hyperlink rendered as plain text | Minor | Formatting |
| CTA U-Pass footnote formatting differs from PDF | Minor | Formatting |
| Merged cells for CTA U-Pass / U-Pass+ rows not perfectly represented | Minor | Structure |
| Refund Schedule links rendered as text instead of actual URLs | Major | Formatting |
| Email address orhelp@uic.edu not formatted as mailto link | Minor | Formatting |
| Arbitrary horizontal rule separator (---) before Endnotes | Minor | Formatting |
| Escaped underscore in URL (guaranteed\_tuition) is a markdown artifact | Minor | Formatting |

**Total: 10 issues (1 critical, 3 major, 6 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 2 minutes 4 seconds |
| Conversion Cost | $0.37 |
| Token Usage | 292,647 tokens |
| Total Pages | 3 |
| Total Edits | 12 |
