# Survival Migration (Chapter 1 from academic book)

## Document Description
Chapter 1 of an academic book on "Survival Migration" that argues for a broader conceptual framework beyond the 1951 Refugee Convention's persecution-based definition. The chapter develops the concept of "survival migration" to encompass people fleeing serious human rights deprivations who fall outside the traditional refugee framework.

## Document Characteristics
- Page count: 10 (pages numbered 10-28 in the book, with a blank final page)
- Content type: Text-heavy academic prose
- Notable features: Two-column layout (left and right pages of an open book scan), footnotes/endnotes, in-text citations, one Venn diagram (Figure 1), italicized terms (*non-refoulement*, *attribution*, *relevance*), running headers ("CHAPTER 1" on even pages, "SURVIVAL MIGRATION" on odd pages), page numbers

## What the Conversion Did Well
- Excellent text extraction overall -- the body text is highly accurate and readable throughout all 10 pages
- Heading hierarchy is generally well preserved (H1 for "SURVIVAL MIGRATION", H2 for major sections like "The Purpose of Refugee Protection", "Limitations of the Existing Refugee Framework", "New Drivers of Displacement", "The Need for a New Concept", H3 for "Environmental Change", "Food Insecurity", "State Fragility", "Beyond Arbitrary Causality")
- Italicized terms like *non-refoulement* are correctly rendered in markdown italics
- In-text academic citations (e.g., Haddad 2008, 47-69) are accurately preserved
- Footnotes are extracted and formatted as markdown footnote references with `[^N]` syntax
- The Venn diagram (Figure 1) has a meaningful alt text description
- Em dashes preserved correctly throughout
- Long, complex paragraphs with nuanced argumentation are faithfully reproduced
- The Notes section at the end is properly formatted

## What the Conversion Could Improve
- **Two-column reading order confusion**: The scanned two-page spread caused some column-interleaving issues. For example, the text from the left page (p.10) and right page (p.11) of the first spread are mostly sequential, but there are places where right-column text from one page appears before the left-column text finishes its logical flow (e.g., around lines 13-14 and 17-19, content from pages 12-13 is interleaved out of order -- the convention definition passage and the "Limitations" section text are shuffled)
- **Paragraph breaks disrupted**: Some paragraphs that span across the page gutter (left-to-right page) are split into separate paragraphs or have their sentences reordered
- **Running headers not stripped**: "CHAPTER 1" and "SURVIVAL MIGRATION" page headers, as well as page numbers (10, 11, 12, etc.), are not present in the output, which is actually good -- but the content that was near those headers sometimes gets displaced
- **Footnote numbering gaps**: Footnotes jump from [^6] to [^10], missing [^7], [^8], [^9] -- these may exist in the original but were not captured
- **Duplicate footnote content**: Footnotes [^12] and [^14] contain identical text ("The point here is that border crossing is a last resort"), and some footnote content appears to be the inline text rather than the actual footnote text from the bottom of the page
- **Footnotes content inaccurate**: The footnote definitions at the end appear to just repeat the surrounding inline text rather than providing the actual footnote content (which would typically be bibliographic references or supplementary notes printed at the bottom of the page)
- **Some sentence-level reordering**: In sections where left-page content continues onto the right page, there are instances where sentences from the right page are inserted before the left-page paragraph concludes (particularly visible in the early pages)
- **Figure 1 caption not fully preserved**: The original caption reads "FIGURE 1. The conceptual relationship of survival migration to refugees and international migration." -- only partially captured in the alt text

## Issues Discovered and Severity

| Issue | Severity | Category |
|-------|----------|----------|
| Two-column reading order confusion — content from different pages interleaved out of order | Critical | Structure |
| Paragraph breaks disrupted — paragraphs spanning page gutter split or sentences reordered | Major | Structure |
| Footnote numbering gaps — footnotes [^7], [^8], [^9] missing between [^6] and [^10] | Critical | Content Accuracy |
| Duplicate footnote content — [^12] and [^14] contain identical text | Major | Content Accuracy |
| Footnote definitions repeat inline text instead of actual footnote content (bibliographic references) | Critical | Content Accuracy |
| Sentence-level reordering in sections where left-page content continues onto right page | Major | Structure |
| Figure 1 caption only partially captured in alt text | Minor | Figures/Images |

**Total: 7 issues (3 critical, 3 major, 1 minor)**

## Performance Benchmarks

| Metric | Value |
|--------|-------|
| Conversion Time | 7 minutes 6 seconds |
| Conversion Cost | $1.21 |
| Token Usage | 991,440 tokens |
| Total Pages | 10 |
| Total Edits | 107 |
