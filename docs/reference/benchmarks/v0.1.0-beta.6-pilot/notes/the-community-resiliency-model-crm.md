# The Community Resiliency Model (CRM)

## Document Description
A single-page infographic from the Trauma Resource Institute explaining the Community Resiliency Model (CRM), which describes biologically-based wellness skills for stabilizing the nervous system. It includes a diagram of the "Resilient Zone" concept showing high and low zone symptoms.

## Document Characteristics
- Page count: 1
- Content type: Infographic with mixed text and visual elements
- Notable features: Teal/green color scheme, DNA helix illustration, Trauma Resource Institute logo (top right), large zone diagram showing nervous system dysregulation with red/black/green lines and labeled zones, bold headings, bulleted symptom lists

## What the Conversion Did Well
- All text content is accurately captured: title, CRM definition, section headings, zone descriptions, and symptom lists
- Heading hierarchy is reasonable (H1 for title, H2 for section headings, H3 for sub-sections)
- The High Zone and Low Zone symptom lists are correctly rendered as bullet lists with accurate content
- The DNA helix image has a good alt text description (figure-1)
- The zone diagram (figure-4) has an excellent, detailed alt text description that conveys the meaning of the chart including color coding, zones, symptoms, and the oscillating pattern
- The "More information at traumaresourceinstitute.com" footer is captured
- Bold formatting is applied to key labels (HIGH ZONE, LOW ZONE, traumaresourceinstitute.com)
- Reading order follows a logical flow from top to bottom

## What the Conversion Could Improve
- **Trauma Resource Institute logo not described**: The logo in the top-right corner showing people under a sunrise/sun with "TRAUMA RESOURCE INSTITUTE" text is not mentioned or described in the markdown (though the organization name appears as text via "traumaresourceinstitute.com").
- **Duplicate diagram content**: The zone diagram content is represented twice -- once as extracted text (lines 25-39 with the symptom lists) and once as the figure-4 image with detailed alt text (line 45). This is somewhat redundant but arguably helpful for accessibility since the text version provides structured data while the image provides visual context.
- **Figure-3 placement**: Figure-3 (line 23) appears to be the upper portion of the zone diagram showing just the resilient zone with the wavy line. It has good alt text. But it creates a split where the zone diagram is presented as two separate figures rather than one cohesive visual.
- **"High & Low Zones" heading level**: Rendered as H3 (`### High & Low Zones:`) but in the PDF it appears at the same visual weight as "Resilient Zone:" which is H2. The heading hierarchy could be more consistent.
- **Color information lost**: The teal/green color scheme, the red arrows showing traumatic triggers, and the green line showing the resilient zone are important visual elements. The figure-4 alt text does mention colors, which is good, but the overall color-coded design of the page is not conveyed.
- **Registered trademark symbol**: The title correctly includes the registered trademark symbol, which is good.

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Trauma Resource Institute logo not described (org name appears in text as URL) | Minor | Accessibility |
| Duplicate diagram content -- zone diagram represented as both text and figure-4 image | Major | Structure |
| Figure-3 splits zone diagram into two separate figures instead of one cohesive visual | Major | Figures/Images |
| "High & Low Zones" heading rendered as H3 instead of H2, inconsistent with "Resilient Zone" | Minor | Structure |
| Color information (teal/green scheme, red arrows, green resilient zone line) lost in conversion | Minor | Formatting |

**Total: 5 issues (0 critical, 2 major, 3 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 0 minutes 35 seconds |
| Conversion Cost | $0.06 |
| Token Usage | 48,096 tokens |
| Total Pages | 1 |
| Total Edits | 5 |
