# Rafael Cintron Ortiz Latino Cultural Center: 2013-14 Year at a Glance

## Document Description
An annual report brochure for the Rafael Cintron Ortiz Latino Cultural Center at the University of Illinois at Chicago, covering the 2013-2014 academic year. It highlights programs, partnerships, community engagement, statistics, and a full listing of public programs.

## Document Characteristics
- Page count: 4 (landscape-oriented brochure pages, likely a folded booklet)
- Content type: Mixed layout with text, photographs, statistics, and program listings
- Notable features: Multi-column layout, bar chart showing visitation growth, numerous photographs, vision/mission statements, demographic attendance data, detailed program calendar listings, colored section headers, logos and QR code

## What the Conversion Did Well
- Extracted the partners and supporters section accurately with all three categories (UIC co-sponsors, student organizations, community engagement)
- Long lists of organization names are faithfully reproduced
- Contact information (address, phone, website) is preserved
- The "creating cultural responses to social conditions" section text is largely accurate
- Sub-sections (affirming diverse identities, heritage garden, engaging in dialogue through the arts) are correctly structured as H3 headings
- Program listings for Zona Abierta, Civic Cinema, Noche de Poetas, Telling Our Stories, and Special Events are captured with dates
- Good alt text on several figures (flower garden photo, butterfly postcards, citizenship event poster)
- The bar chart figure has alt text describing the visitation numbers
- Footnote-style statistics ("We presented 61 public programs", "We facilitated 18 civic dialogues and tours", etc.) are not in the markdown but the narrative captures much of the same info

## What the Conversion Could Improve
- The document title/cover ("rafael cintron ortiz latino cultural center 2013-14 year at a glance") is rendered as plain bold text (lines 22-28) rather than as a proper H1 heading -- there is no H1 in the document at all
- The "engaging campus and local communities" section header from page 2 is missing entirely -- the narrative text from that section appears to be merged into the general content
- The "strengthening our foundation" section header from page 2 is missing -- the text about CCUSC and staff expansion is not present in the markdown
- The vision and mission statements from the right column of page 2 are not extracted as text -- they are only captured as part of figure-4's alt text
- Key statistics are missing from the body text: the "Who attended" demographic breakdown (African American/Black 4.2%, Latino/a 53.7%, etc.) and "What they attended" breakdown (2,483 attendees at LCC public events 17%, etc.) from page 2
- The highlighted achievement metrics (61 public programs, 18 civic dialogues, 40-member Ambassador Group, 22 student interns, 4 Graduate Assistants, 4 student workers, 19 faculty, 37 community schools, 21 UIC student organizations) from page 2 are missing
- The "14,707 visitations this year" highlighted statistic is missing from the text
- The visitation trend data (10,585 in 2011-2012, 12,459 in 2012-2013, 14,707 in 2013-2014) is only in the figure alt text, not in the body
- The "latinocultural.uic.edu" large URL from the bottom of page 2 is not prominently captured
- The LCC staff caption ("LCC staff (left to right): Yehimy Montes, Program Coordinator; Dr. Rosa Cabrera, Director; Edith Tovar, Program Coordinator; Mario Lucero, Assistant Director") from page 2 is missing
- Page 1 has a QR code (figure-1) with empty alt text — the QR code destination URL is not provided anywhere in the text, making this functional content completely inaccessible
- Several text errors from OCR or AI correction: "Shadows then Light: Cultural Citizenship as the Ground" should be "on the Ground" (line 54); "Features Undocumented Alliance" should be "Fearless Undocumented Alliance" (line 62); "ongoing sustainable practices" should be "growing sustainable practices" (line 64); "opening presenting the author portrait" should be "continue presenting the outdoor portrait" (line 62)
- "TELLING OUR STORIES" section (line 106) appears truncated -- the "UndocuLove Messages 2.13.2014" entry is missing and "Inclusive Managers" appears garbled at the end
- In SPECIAL EVENTS: "Kundalini Yoga an Espanol with Tel Dharam" should be "en Espanol with Taj Dharam"; "Honoring Mandala" should be "Honoring Mandela"; "22nd Annual IFC Chicago Latin Film Festival" should be "UIC Chicago Latino Film Festival"; "Transcending Seasonality: Initiative Kickoff" should be "Transcending Masculinity Initiative Kickoff"; "the DREAM &" should be "the DREAM 9:"
- In CIVIC CINEMA: "Sierra Palada" should be "Serra Pelada"; "Island Mountain" should be "Naked Mountain"
- In ZONA ABIERTA: "Lula Martinez" should be "Lulu Martinez" (or Lulu with accent); "Valijante" should be "Vejigante"
- Many photographs (figures 8-14, 15-20) have empty alt text -- these are event/program photos that should be described
- The reading order is somewhat jumbled between pages, with page 1 back matter (partners) appearing before the cover/title content

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Document title rendered as plain bold text instead of H1 heading | Major | Structure |
| "Engaging campus and local communities" section header missing entirely | Critical | Content Accuracy |
| "Strengthening our foundation" section header and content missing | Critical | Content Accuracy |
| Vision and mission statements not extracted as text (only in figure alt text) | Major | Content Accuracy |
| Key demographic statistics ("Who attended" breakdown) missing | Critical | Content Accuracy |
| Achievement metrics (61 programs, 18 dialogues, etc.) missing | Critical | Content Accuracy |
| "14,707 visitations this year" statistic missing from text | Major | Content Accuracy |
| Visitation trend data only in figure alt text, not in body | Major | Content Accuracy |
| "latinocultural.uic.edu" URL not prominently captured | Minor | Formatting |
| LCC staff caption missing | Major | Content Accuracy |
| QR code (figure-1) has empty alt text — destination URL not in text, functional content inaccessible | Major | Accessibility |
| OCR text errors: "Shadows then Light" should be "on the Ground" | Critical | Content Accuracy |
| OCR text errors: "Features Undocumented Alliance" should be "Fearless" | Critical | Content Accuracy |
| OCR text errors: "ongoing sustainable practices" should be "growing" | Major | Content Accuracy |
| "TELLING OUR STORIES" section appears truncated | Major | Content Accuracy |
| Multiple event name errors in SPECIAL EVENTS (Kundalini, Honoring Mandala, etc.) | Critical | Content Accuracy |
| Event name errors in CIVIC CINEMA ("Sierra Palada" should be "Serra Pelada") | Major | Content Accuracy |
| Event name errors in ZONA ABIERTA ("Lula Martinez", "Valijante") | Major | Content Accuracy |
| Many photographs (figures 8-20) have empty alt text | Major | Accessibility |
| Reading order jumbled between pages | Major | Structure |

**Total: 20 issues (7 critical, 11 major, 2 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 1 minute 44 seconds |
| Conversion Cost | $0.21 |
| Token Usage | 164,124 tokens |
| Total Pages | 4 |
| Total Edits | 11 |
