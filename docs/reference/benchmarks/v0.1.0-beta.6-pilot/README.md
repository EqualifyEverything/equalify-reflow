# v0.1.0-beta.6 pilot benchmark

Equalify Reflow v0.1.0-beta.6 was benchmarked against 30 real-world University of Illinois Chicago documents selected for diversity across content types, page counts, and layout complexity. Each converted output was reviewed manually against the source PDF and scored for issues across Content Accuracy, Structure, Formatting, and Accessibility.

This folder is the authoritative record of that run:

- [`manifest.txt`](manifest.txt) — the 30-document corpus (see [Reproducing](#reproducing) for why PDFs are not checked in)
- [`summary.json`](summary.json) — machine-readable per-document cost, tokens, elapsed, edits
- [`per-document-scores.csv`](per-document-scores.csv) — flat table of performance + quality metrics
- [`notes/`](notes/) — per-document qualitative review notes with severity-graded issue lists

## Headline results

| Metric | Value |
|---|---|
| Documents | 30 |
| Pages | 175 |
| Total conversion cost | $12.99 |
| Total tokens | 10,422,111 |
| Total edits applied | 1,340 |
| Total issues found | 235 |
| Average cost per document | $0.43 |
| Average cost per page | $0.07 |
| Average conversion time | 2 min 56 sec |
| Success rate | 100% (30/30 completed) |

### Issue severity breakdown

| Severity | Count | Share |
|---|---|---|
| Critical | 45 | 19.1% |
| Major | 102 | 43.4% |
| Minor | 88 | 37.4% |

### Best and most challenging conversions

**Fewest issues:** State Infrastructure Program (3 issues, 0 critical) · CEDA Utility Assistance (4 issues, 1 critical) · RELS 225 Jews in Islamic Lands (4 issues, all minor)

**Most challenging:** Latino Cultural Center Year at a Glance (20 issues, 7 critical) · The Future of Work (13 issues, 3 critical) · La Opinion Latina Newsletter (13 issues, 8 critical — bilingual scanned newsprint)

## Methodology

### Document selection

The corpus was curated from UIC department sites to represent the full range of content types the pipeline is expected to handle:

| Type | Count | Examples |
|---|---|---|
| Academic book chapter | 4 | Boxing and Masculinity, Survival Migration, Future of Work, Who Is a Refugee |
| Event poster / flyer | 4 | Mesoamerican Archaeology, Women's Rights, Mural Tours, Language Data |
| Infographic | 4 | Transgender Youth, Heat Stress, CRM, Muslim Civic Engagement |
| Policy / procedure document | 3 | Diabetes Screening, Patient Complaint, Early Retirement Agreement |
| Course material / syllabus | 3 | RELS 225, BIOS 343, Advising Millennial Students |
| Brochure / annual report | 2 | Latino Cultural Center Year at a Glance, Public Health Summer Program |
| Presentation slides | 2 | Education of Alice Hamilton, Professional Licensure Disclosures |
| Formal letter | 1 | Senate Democrats to Bezos |
| Report with charts | 1 | Electricity Prices in Australia |
| Policy brief | 1 | State Infrastructure |
| Guidelines document | 1 | ICMJE Recommendations |
| Newsletter (bilingual, scanned) | 1 | La Opinion Latina |
| Tuition / fees schedule | 1 | UIC Graduate Rates |
| Commemorative poster | 1 | Who Was Rafael Cintron Ortiz |
| Flyer with directory table | 1 | CEDA Utility Assistance |

Page counts span 1 to 20 pages. Both born-digital and scanned documents are included.

### Pipeline run

All 30 documents were submitted to the deployed v0.1.0-beta.6 instance using `scripts/batch_run.py --manifest <this-folder>/manifest.txt` at concurrency 2. Models: Claude Haiku 4.5 on AWS Bedrock across all pipeline agents (see [`docs/reference/model-tiers.md`](../../model-tiers.md)). PII findings were auto-approved.

### Review rubric

Each conversion was compared line-by-line against the source PDF by a human reviewer. Issues were categorized and severity-rated:

| Category | What counts |
|---|---|
| Content Accuracy | OCR errors, missing text, duplicated content, wrong characters, lost punctuation |
| Structure | Heading hierarchy, reading order, section boundaries, list structure, footnote placement |
| Formatting | Tables, emphasis, whitespace, HTML entity artifacts, running headers |
| Accessibility | Alt text quality on non-decorative figures, image-embedded text extraction |

| Severity | Threshold |
|---|---|
| Critical | Changes the meaning of the document, or makes a section unreadable |
| Major | Noticeably degrades reading experience or accessibility but meaning is preserved |
| Minor | Cosmetic or easily-ignored artifact |

Per-document reviews are in [`notes/`](notes/). Each follows the same template: description, characteristics, what went well, what could improve, graded issue table, performance numbers.

## Performance

### Cost distribution

| Cost range | Documents | Typical page count |
|---|---|---|
| $0.00 – $0.10 | 10 | 1 page |
| $0.10 – $0.50 | 12 | 2–8 pages |
| $0.50 – $1.00 | 3 | 10–15 pages |
| $1.00 – $1.74 | 5 | 8–20 pages |

Cheapest: Who Was Rafael Cintron Ortiz — $0.00 (1 page). Most expensive: ICMJE Recommendations — $1.74 (20 pages, 316 edits).

### Time

Fastest: 20 seconds (Who Was Rafael Cintron Ortiz, 1 page). Slowest: 8 min 21 sec (The Future of Work, 11 scanned pages, 52 footnotes). Median: 2 min 3 sec. Scanned multi-column academic chapters consistently took the longest; born-digital single-page documents typically completed in under a minute.

### Throughput projection

At these unit economics, processing a catalog of 1,000 typical university documents (averaging 5 pages) would cost roughly $400–$450 and consume about 45 hours of compute time. Jobs run in parallel on cloud infrastructure, so wall-clock time scales down with concurrency.

## Quality by document type

| Type | Avg issues | Primary problems |
|---|---|---|
| Academic book chapters | 10–13 | Footnote errors, reading order, duplicate endnotes |
| Infographics | 6–7 | Lost spatial layout, orphaned figures |
| Policy documents | 5–10 | Heading hierarchy, missing hyperlinks, table formatting |
| Posters / flyers | 4–8 | Informational content trapped inside images |
| Presentation slides | 8–11 | Slide boundary confusion, embedded text not extracted |

Full per-document breakdown is in [`per-document-scores.csv`](per-document-scores.csv) and [`notes/`](notes/).

## Findings and improvement roadmap

Issues cluster into nine categories. Each is documented with affected documents, current code pointers, and proposed changes in [issue #82](https://github.com/EqualifyEverything/equalify-reflow/issues/82) — that issue is the working roadmap and is updated as items land.

The highest-leverage categories for the next pilot re-run:

1. **Footnote & endnote handling** — the single most impactful area for academic content. Affects ~30 issues across 6+ documents (missing notes, duplicated notes, swapped order).
2. **Hyperlink extraction** — systematic gap. No dedicated handling exists today; URLs land as plain text in 8+ documents.
3. **Low-quality scan gating** — *La Opinion Latina* alone produced 8 critical issues from catastrophic OCR on bilingual newsprint. A quality threshold that hard-rejects unsuitable scans would prevent a class of failure that the pipeline cannot recover from.
4. **Figure alt text quality** — empty alt text on informational figures (event dates, QR codes, social media handles); text embedded in images not extracted.
5. **Heading hierarchy** — document titles missing when embedded in banners; plain text occasionally promoted to headings.
6. **Table formatting** — particularly glossaries and ToCs being mis-structured as tables.
7. **Multi-column reading order** — hardest problem; scanned academic chapters are out of scope for V1 and are expected to benefit most from the scan gating work above.
8. **Presentation and poster layouts** — slide boundaries and poster zones flatten unhelpfully.
9. **Minor formatting** — HTML entities, running header remnants, emphasis loss.

Code pointers for each category are in [issue #82](https://github.com/EqualifyEverything/equalify-reflow/issues/82).

## V1 scope

Equalify Reflow v0.1 targets born-digital and cleanly-scanned course materials: policy documents, syllabi, event flyers, infographics, single-column reports, and presentation slides. Within that scope the pipeline achieved 100% completion and an average of fewer than 8 issues per document.

Document classes that sit outside V1 and are expected to improve with targeted work in future releases:

- Scanned multi-column academic book chapters
- Low-quality bilingual newsprint scans
- Heavy infographic and poster layouts where spatial relationships carry meaning

The pipeline already warns on scanned and low-text-density documents via [`src/services/pdf_classifier.py`](../../../../src/services/pdf_classifier.py) (`FINDING_SCANNED`, `FINDING_LOW_TEXT_DENSITY`). Strengthening these into explicit rejection thresholds for the catastrophic cases is tracked as category 3 above.

## Reproducing

1. Obtain the corpus (the 30 PDFs are not redistributed — several are third-party copyrighted works). UIC team members should ask in the project channel; external reproducers can substitute documents matching the type and page-count profile in [`manifest.txt`](manifest.txt).
2. Place the PDFs under `samples/` beside this folder's manifest, using the filenames the manifest lists.
3. Export `BATCH_API_KEY` and (optionally) `BATCH_API_URL` to point at the API you want to benchmark.
4. Run the harness:

   ```bash
   uv run scripts/batch_run.py \
       --manifest docs/reference/benchmarks/v0.1.0-beta.6-pilot/manifest.txt \
       --output my-run/
   ```

5. Compare `my-run/summary.json` against [`summary.json`](summary.json) in this folder. Differences are expected when model versions or prompts have changed between releases — that comparison is the point of the benchmark.

Full workflow and tips: [`docs/how-to/run-the-benchmark.md`](../../../how-to/run-the-benchmark.md).
