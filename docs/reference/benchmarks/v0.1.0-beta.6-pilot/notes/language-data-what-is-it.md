# Language Data: What Is It, How Do We Analyze It, and What Can It Tell Us?

## Document Description
A promotional poster/flyer for a Zoom lecture in the UIC Department of Linguistics "Lectures on Linguistics and Language" series. The talk is by Anna Tsiola, scheduled for October 21 at 4:00pm-5:30pm, about how psycholinguists elicit, collect, and analyze language data.

## Document Characteristics
- Page count: 1
- Content type: Mixed layout poster/flyer
- Notable features: Large decorative title text, decorative background blobs, speaker headshot photo, UIC Liberal Arts and Sciences / Department of Linguistics logo, Zoom meeting credentials, multi-column layout

## What the Conversion Did Well
- Extracted the main title and subtitle accurately
- Captured the full abstract/description paragraph faithfully
- Extracted the Zoom meeting ID and passcode
- Captured the speaker bio paragraph accurately
- Identified "Lectures on Linguistics and Language" as a heading
- Extracted the speaker headshot and logo as separate figures
- Figure-2 has a useful alt text describing the meeting credentials

## What the Conversion Could Improve
- The date "Oct. 21" and time "4:00pm - 5:30pm" were not included in the markdown text; they only appear in figure-1 which has no alt text. These are critical event details that should be in the text.
- Figure-1 (the date/time graphic) has empty alt text — it should describe "October 21, 4:00pm - 5:30pm". Since the date and time are not present anywhere in the markdown text, this missing alt text means the information is completely inaccessible.
- Figure-4 (UIC logo) has empty alt text — it should describe "UIC Liberal Arts and Sciences, Department of Linguistics logo". The department name does not appear anywhere in the markdown text, so this information is lost.
- The reading order places the meeting credentials between the abstract and the speaker bio, which is acceptable but the date/time info is lost entirely from the text flow
- The "Zoom Meeting" text visible in the decorative background of the poster was not captured (minor, since it is decorative)
- The word "psycholinguists" in the bio paragraph appears as "psycholinguistics" in the PDF OCR text but in the original poster it says "psycholinguists" — the markdown has "psycholinguistics" which may be a correction or a misread (the PDF itself says "psycholinguists")
- The series name "Lectures on Linguistics and Language" is marked as h2 but appears more as a decorative label/series title rather than a subsection

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Date "Oct. 21" and time "4:00pm - 5:30pm" not included in markdown text | Critical | Content Accuracy |
| Figure-1 (date/time graphic) has empty alt text — date and time not in text, information completely inaccessible | Major | Accessibility |
| Figure-4 (UIC dept logo) has empty alt text — department name not in text, information lost | Major | Accessibility |
| Date/time info lost entirely from text flow due to reading order | Major | Structure |
| "Zoom Meeting" decorative background text not captured | Minor | Content Accuracy |
| "psycholinguists" rendered as "psycholinguistics" — possible misread | Major | Content Accuracy |
| Series name "Lectures on Linguistics and Language" marked as h2 instead of decorative label | Minor | Structure |

**Total: 7 issues (1 critical, 4 major, 2 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 0 minutes 30 seconds |
| Conversion Cost | $0.05 |
| Token Usage | 39,584 tokens |
| Total Pages | 1 |
| Total Edits | 3 |
