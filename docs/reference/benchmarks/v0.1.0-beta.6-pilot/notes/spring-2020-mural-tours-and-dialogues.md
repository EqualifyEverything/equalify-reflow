# Spring 2020 Mural Tours & Dialogues

## Document Description
A promotional poster for the UIC Rafael Cintron Ortiz Latino Cultural Center's Spring 2020 mural tours and arts-based civic dialogues program, inviting UIC classes and groups to visit the LCC for guided tours of the "El Despertar de las Americas" mural and facilitated dialogues on Im/migration or Environmental and Climate Justice.

## Document Characteristics
- Page count: 1
- Content type: Mixed layout event poster
- Notable features: Large hero photo of students viewing a mural, bold decorative title text, two-column TIME/WHERE info bar, organizational logo, social media icon bar at bottom, vivid magenta/yellow color scheme

## What the Conversion Did Well
- Extracted all body text accurately (program descriptions, contact info, accessibility note)
- Preserved the italic formatting on "El Despertar de las Americas"
- Correct heading hierarchy for "LCC Mural Tour" and "Arts-Based Civic Dialogues" as h3 under the main title
- Bold formatting on phone numbers and URLs preserved
- TIME and WHERE details captured correctly
- Reservation and contact information fully captured
- The "Rafael Cintron Ortiz Latino Cultural Center" name and website extracted as text at the bottom

## What the Conversion Could Improve
- Figure-1 (the large hero photo showing students on a mural tour) has empty alt text — this is the most prominent visual element and should describe "Students viewing the El Despertar de las Americas mural at the Latino Cultural Center"
- Figure-2 (UIC Rafael Cintron Ortiz Latino Cultural Center logo) has empty alt text
- Figure-3 (social media bar with latinocultural.uic.edu, Facebook, Twitter, Instagram, Snapchat, YouTube icons, and @UICLCC) has empty alt text — the social media handles should ideally be rendered as text since they are contact information
- The social media platforms visible in the footer (Facebook, Twitter, Instagram, Snapchat, YouTube, @UICLCC) are not captured as text — only "latinocultural.uic.edu | @UICLCC" appears
- The "&" between "MURAL TOURS" and "DIALOGUES" in the main title is not preserved — the markdown shows "## MURAL TOURS & DIALOGUES" which is adequate but the visual prominence of the ampersand as a design element is lost (this is minor)
- The horizontal rules (---) used to separate sections are a reasonable choice but the original poster uses colored bars/bands rather than simple dividers

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Figure-1 (hero photo of students on mural tour) has empty alt text | Major | Accessibility |
| Figure-2 (Latino Cultural Center logo) has empty alt text | Minor | Accessibility |
| Figure-3 (social media bar with icons and @UICLCC) has empty alt text | Major | Accessibility |
| Social media platforms (Facebook, Twitter, Instagram, Snapchat, YouTube) not captured as text | Major | Content Accuracy |
| The "&" design element between "MURAL TOURS" and "DIALOGUES" loses visual prominence | Minor | Formatting |
| Horizontal rules used instead of colored bars/bands from original poster | Minor | Formatting |

**Total: 6 issues (0 critical, 3 major, 3 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 1 minutes 5 seconds |
| Conversion Cost | $0.05 |
| Token Usage | 38,448 tokens |
| Total Pages | 1 |
| Total Edits | 7 |
