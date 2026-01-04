# V5 Pipeline Optimization Plan

Based on analysis of the Docling Technical Report conversion output.

## Output Quality Assessment

### Strengths (Working Well)
1. **Alt text generation** - High-quality, detailed descriptions (50-150 words)
2. **Table preservation** - Complex tables with multi-column headers preserved
3. **Heading hierarchy** - H1 → H2 → H3 → H4 correctly structured
4. **Code blocks** - Python code preserved with syntax
5. **Reading order** - Multi-column layouts correctly linearized
6. **References** - Bibliographic entries intact

### Issues Identified

#### P1 - Critical (Affects Accessibility)
1. **Figure numbering lost** - Original "Figure 1:", "Figure 2:" not preserved in alt text
2. **Long alt texts** - Some 150+ words, may overwhelm screen readers
3. **Alt text lacks figure context** - Doesn't mention which figure number it describes

#### P2 - Important (Quality)
4. **Page break artifacts** - `---` separators can interrupt mid-sentence
5. **PDF encoding issues** - "R¨uschlikon" instead of "Rüschlikon" (umlaut rendering)
6. **Missing captions** - Figure captions could be linked to generated alt text

#### P3 - Nice to Have
7. **Reference URL cleanup** - Some URLs have escaping artifacts
8. **Table caption association** - Better linking of "Table X:" to table content
9. **Decorative detection** - Could skip purely decorative elements

---

## Optimization Tasks

### Phase 1: Figure Context Enhancement
**Goal:** Add figure numbering and improve alt text context

#### Task 1.1: Extract Figure Numbers from Docling
- Docling provides figure/picture elements with ordering
- Track figure index per page during element extraction
- Pass figure number to alt text agent

#### Task 1.2: Improve Alt Text Prompt
- Add figure number to prompt: "Generate alt-text for Figure 3"
- Add preceding/following text context
- Request structured output: caption + description

#### Task 1.3: Alt Text Length Control
- Add max word count to prompt (target: 75-100 words)
- Provide "extended description" option for complex figures
- Implement validation in FixResult

### Phase 2: Page Assembly Improvements
**Goal:** Smarter page joining without breaking content

#### Task 2.1: Smart Page Breaks
- Only insert `---` between major sections (H1, H2)
- Detect paragraph continuation across pages
- Use heading detection to identify natural breaks

#### Task 2.2: PDF Encoding Fix
- Post-process common encoding issues (ü → ü, etc.)
- Use character normalization (NFD → NFC)
- Fix common LaTeX artifacts

### Phase 3: Figure-Caption Association
**Goal:** Link captions to figures for better context

#### Task 3.1: Caption Detection
- Detect "Figure X:" patterns in surrounding text
- Extract caption text from markdown
- Associate with corresponding figure placeholder

#### Task 3.2: Unified Figure Block
- Output format: `**Figure X: [Caption]**\n\n[Alt text description]`
- Maintains semantic relationship
- Better for accessibility

### Phase 4: Performance Optimization
**Goal:** Reduce latency and cost

#### Task 4.1: Parallel Context Gathering
- Run outline extraction and page summaries in parallel
- Current: Sequential context → issues → fixes
- Target: Parallel where dependencies allow

#### Task 4.2: LLM Call Batching
- Batch multiple figure descriptions per page
- Single image, multiple elements
- Reduces API overhead

#### Task 4.3: Caching
- Cache figure descriptions by image hash
- Useful for repeated processing during development
- Optional in production

---

## Implementation Priority

| Phase | Effort | Impact | Priority |
|-------|--------|--------|----------|
| 1.1-1.2 | Medium | High | **Now** |
| 1.3 | Low | Medium | Now |
| 2.1 | Medium | Medium | Next |
| 2.2 | Low | Low | Next |
| 3.1-3.2 | High | High | Later |
| 4.1-4.3 | High | Medium | Later |

---

## Next Steps

1. **Immediate:** Implement figure numbering (Task 1.1-1.2)
2. **This week:** Alt text length control (Task 1.3)
3. **Next sprint:** Smart page breaks (Task 2.1)

## Metrics to Track
- Alt text word count distribution
- Figure detection accuracy
- Processing time per page
- User satisfaction (manual review)
