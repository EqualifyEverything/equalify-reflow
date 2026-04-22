# BIOS 343: Animal Physiology - Course Guide and Syllabus, Fall 2023

## Document Description
A 13-page undergraduate course syllabus for BIOS 343 (Animal Physiology) at UIC. Covers instructor info, teaching philosophy, course objectives, expectations, assignments, grading policy, a detailed 16-week course schedule, classroom policies, accommodations, and student resources.

## Document Characteristics
- Page count: 13
- Content type: Text-heavy with decorative illustrations and tables
- Notable features: Watercolor anatomical illustrations (brain, heart, eye, ribcage, stomach, reproductive system), two course schedule tables (weeks 1-7 in HTML table, weeks 8-16 in markdown table), grading breakdown tables, weekly schedule table, bulleted and numbered lists, bold/italic formatting, hyperlinks to UIC resources, smiley face emoji in "Extra Credit" heading

## What the Conversion Did Well
- Comprehensive text extraction: virtually all syllabus text is accurately captured across all 13 pages
- Heading hierarchy is generally well-structured with H1, H2, and H3 levels used appropriately
- Bulleted lists throughout (course objectives, skills, policies, resources) are properly formatted
- The quiz/knowledge check example table (Unit 1, Weeks 1-4) is clean and accurate
- Grading breakdown table with point values and percentages is correctly rendered
- Extra credit opportunities table is accurate
- The weekly schedule table is properly formatted
- Course schedule for weeks 1-7 uses an accessible HTML table with caption, thead, scope attributes -- good semantic markup
- Course schedule for weeks 8-16 uses markdown table format and captures all dates, topics, assignments, assessments, and textbook references
- Bold formatting is preserved for key terms (e.g., "Answering emails", "Do-not-reply emails", "Late Policy", "Quiz Creation", "Science in the News")
- Italic formatting for book titles (New York Times, Science News) is preserved
- The turkey emoji for Thanksgiving is captured
- The "NO MAKE-UP ASSESSMENTS" bold notice from the PDF (visible on page 5 after the example table) is missing from the markdown -- this is a content loss of a prominent policy statement
- Decorative anatomical illustrations have good alt text descriptions (brain MRI, heart, eye cross-section, ribcage, stomach)

## What the Conversion Could Improve
- The URL https://www.mypronouns.org/whatand-why is broken -- the original PDF shows "what-and-why" hyphenated across a line break, and the conversion dropped the hyphen, producing "whatand-why". This was noted in previous feedback and remains unfixed.
- The course schedule is split into two different formats: weeks 1-7 use an accessible HTML table (with caption, thead, scope — correctly used for complex structure), while weeks 8-16 use a markdown table. Ideally both halves would use the same HTML table format for consistency, since the HTML table provides better accessibility semantics.
- The column headers differ between the two schedule table halves -- the HTML table uses "Topic", "Assignments", "Assessment", "Textbook" while the markdown table uses "Topics", "Materials", "Assessments", "Points". The PDF shows consistent headers throughout. The markdown table's "Points" column appears to actually contain textbook chapter references, not point values.
- The "NO MAKE-UP ASSESSMENTS without legitimate excuse (e.g., death in the family, religious holiday)" bold notice that appears prominently in the PDF after the example quiz schedule table is completely missing from the markdown.
- Several hyperlinks from the PDF are lost: "Community Standards", "academic integrity", "UIC Student Disciplinary Policy", "Tutoring Resources", "UIC Library", "UIC Library Research Guides", "Offices", "Student Guide for Information Technology", "First-at-LAS", "U&I Care Program", "UIC Safe App", "UIC Safety Tips and Resources", "Night Ride", "Emergency Communications", "campus policy", "this form" are all rendered as plain text without URLs in the markdown, whereas the PDF has them as clickable links.
- The `<!-- image -->` comment on line 316 appears to be a placeholder where an anatomical illustration (the reproductive system watercolor) should be referenced as a figure. This image is present in the PDF on the attendance page but not properly linked.
- The Fabrication URL on line 124 contains an escaped underscore (DOS\_Student) and the Academic Integrity URL on line 128 contains `&amp;` instead of `&`, both artifacts of HTML encoding leaking into markdown.
- The grade scale (line 197) is rendered as a single continuous line rather than a structured list or table as it appears in the PDF, making it hard to read.
- The "Considering this, any requests for grade 'bumps' will not be honored" text is underlined and bold in the PDF but only rendered without underline emphasis in the markdown.
- The instructor's email address is blank (line 12: "Email:") -- this matches the PDF which also shows "Email:" with the address redacted/blank, so this is actually correct but worth noting.

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Broken URL: "whatand-why" should be "what-and-why" (dropped hyphen) | Major | Content Accuracy |
| Course schedule split into two different formats (HTML table vs markdown table) | Major | Structure |
| Column headers differ between the two schedule table halves | Major | Structure |
| "NO MAKE-UP ASSESSMENTS" bold policy notice completely missing | Critical | Content Accuracy |
| Multiple hyperlinks rendered as plain text without URLs (Community Standards, academic integrity, etc.) | Major | Formatting |
| `<!-- image -->` placeholder where reproductive system illustration should be referenced | Major | Figures/Images |
| Escaped underscore in Fabrication URL (DOS\_Student) and `&amp;` in Academic Integrity URL | Minor | Formatting |
| Grade scale rendered as single continuous line instead of structured list/table | Minor | Formatting |
| "Requests for grade bumps" text missing underline emphasis from PDF | Minor | Formatting |

**Total: 9 issues (1 critical, 5 major, 3 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 3 minutes 30 seconds |
| Conversion Cost | $0.77 |
| Token Usage | 647,109 tokens |
| Total Pages | 13 |
| Total Edits | 69 |
