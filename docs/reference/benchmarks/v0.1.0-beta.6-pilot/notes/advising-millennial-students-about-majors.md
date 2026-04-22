# In Their Own Words: Best Practices for Advising Millennial Students about Majors

## Document Description
A peer-reviewed academic journal article from the NACADA Journal (Volume 32(2), Fall 2012) presenting qualitative research on how Millennial generational traits influence college students' major selection and advising preferences. The authors recommend a split-model advising system with both staff advisors and faculty mentors.

## Document Characteristics
- Page count: 10
- Content type: Text-heavy academic article with two-column layout
- Notable features: Two-column academic journal format, italic subheadings, block quotes (indented student quotes), bullet list of Millennial traits, running headers ("Millennial Traits" / "Montag et al."), running footers with page numbers and journal citation, abstract in italics, references section in two-column format, no figures or tables

## What the Conversion Did Well
- All body text is captured accurately and completely across all 10 pages
- Heading hierarchy is well-structured: H1 for title, H2 for major sections (Literature Review, Method, Results, Discussion, Limitations, Conclusion, References, Authors' Notes), H3 for subsections
- Author names and affiliations are correctly captured
- The abstract and keywords are properly extracted and placed at the beginning
- The seven Millennial traits bullet list is correctly formatted with bullet points
- Block quotes from student focus group participants are preserved
- In-text citations are accurately reproduced (e.g., "(Broadbridge, 1996; Kramer, Higley, & Olsen, 1994)")
- Reading order is correct despite the two-column layout
- The references section is complete and well-formatted with proper italic markup for journal titles and book titles
- The "&amp;" HTML entity in "Hemwall &amp; Trachte" (line 23) -- this is an encoding artifact; in the PDF it is simply "&"
- Running headers ("Millennial Traits", "Montag et al.") and page footers ("NACADA Journal Volume 32(2) Fall 2012" and page numbers) are correctly stripped

## What the Conversion Could Improve
- The HTML entity `&amp;` appears on line 23 instead of a plain ampersand in "Hemwall & Trachte" -- this should be `&`
- There are a few paragraph breaks that appear mid-sentence due to page/column breaks in the original PDF. For example, lines 27-29 split the CAS standards list across a paragraph break ("d) assisting students in monitoring their own progress toward established goals;" then new paragraph "e) helping students understand..."). Similarly, lines 69-71 split "Hsieh &" / "Shannon, 2005)" across paragraphs, and lines 93-95 split "As Natalie" / "recommended:" across paragraphs, and lines 121-123 split Sarah's quote. Lines 137-139 split "Achievement orientation was present in 34" / "comments by 19 respondents" across paragraphs. Lines 151-153 similarly split mid-sentence. These breaks disrupt readability.
- The journal header information ("26 NACADA Journal Volume 32(2) Fall 2012") from the first page is not included, which is acceptable, but the volume/issue/date metadata could be useful context.
- The italic formatting of subsection headings in the PDF (e.g., "Specialness and Need for Personalization", "Conventionally Motivated", "Optimistic", etc. under Results) and sub-section headings under Method ("Participants", "Instruments", "Procedure", "Data Analysis") are rendered as H3 headings in markdown, which is a reasonable choice, though the original uses italics rather than bold for these.
- The "Achievement Oriented" subsection heading (line 135) loses its italic formatting from the original and is rendered as plain text rather than as an H3 heading like the other trait subsections -- it appears as a standalone line rather than a proper heading.
- The abstract in the PDF is presented in italics; the markdown renders it as plain text, which loses that visual distinction from the body text.
- No email link is created for the contact address tamara.montag@gmail.com on the last line.

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| HTML entity `&amp;` appears instead of plain ampersand in "Hemwall & Trachte" | Minor | Formatting |
| Multiple paragraph breaks mid-sentence due to page/column breaks (lines 27-29, 69-71, 93-95, 121-123, 137-139, 151-153) | Major | Structure |
| "Achievement Oriented" subsection heading not rendered as H3 like other trait subsections | Major | Structure |
| Abstract rendered as plain text instead of italics as in original PDF | Minor | Formatting |
| No email link created for tamara.montag@gmail.com | Minor | Formatting |

**Total: 5 issues (0 critical, 2 major, 3 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 2 minutes 40 seconds |
| Conversion Cost | $0.49 |
| Token Usage | 383,605 tokens |
| Total Pages | 10 |
| Total Edits | 112 |
