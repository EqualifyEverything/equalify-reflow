# Interactive Review Session

Walk through remediation checklist items with the PDF visible for reference.

## Arguments

`$ARGUMENTS`

**Required:** `--job=UUID` - The job ID to review

**Optional:**
- `--auto`: Let AI decide without asking (skips collaboration)
- `--focus=X`: Focus on specific categories (e.g., `--focus=typography`, `--focus=figures`)
- `--skip-agreed`: Auto-accept items where agent confidence > 95%, ask for rest

---

## What This Does

Unlike `/e2e/run` which force-applies reviews, this command creates a **collaborative review session** where the AI and human work together:

1. Opens the source PDF in VS Code for visual reference
2. Loads the PDF into AI context for informed analysis
3. For each checklist item:
   - AI presents the item with context and its recommendation
   - AI **pauses and asks you** what to do (using AskUserQuestion)
   - You make the final decision
   - AI applies your choice
4. Applies all decisions to generate the final markdown

**This is human-in-the-loop review** - the AI assists but you decide.

**Duration:** 2-10 minutes depending on item count and discussion

---

## Phase 1: Setup

**Get job info and validate status:**
```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}"
```

Must be in `needs_review` status. If not, report current status and stop.

**Get the source PDF path from job metadata and open it:**
```bash
# Open PDF in VS Code for user reference
code {PDF_PATH}
```

**Load PDF into AI context:**
Use the Read tool to read the PDF file so the AI can see the original document and make informed decisions about formatting, figures, tables, etc.

---

## Phase 2: Load Checklist

**Get checklist summary:**
```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}/checklist/summary"
```

**Get full checklist:**
```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}/checklist"
```

**Report:**
```
════════════════════════════════════════════════════════════
  INTERACTIVE REVIEW SESSION
════════════════════════════════════════════════════════════
  Job: {JOB_ID}
  Items: {total} ({categories})
  PDF: Opened in editor
════════════════════════════════════════════════════════════
```

---

## Phase 3: Review Each Item

For each unreviewed item, present it clearly:

```
────────────────────────────────────────────────────────────
  ITEM {n}/{total} | {agent} | Confidence: {confidence}%
────────────────────────────────────────────────────────────

QUESTION:
  {question}

CONTEXT (from page {page_num}):
  "{context}"

AGENT REASONING:
  {agent_recommendation}

OPTIONS:
  [1] {option_1_label} {✓ recommended}
  [2] {option_2_label}
  [3] Custom input...
  [4] Skip this item

────────────────────────────────────────────────────────────
```

**Collaborative Decision Process (default):**

For EACH item, use the **AskUserQuestion tool** to pause and ask the user:

```
Present the item context, then ask:

Question: "How should we handle this?"
Options:
  - Accept recommendation ({recommended_option})
  - Use alternative ({other_option})
  - Skip this item
  - (User can provide custom input via "Other")
```

Wait for user response before proceeding to next item.

This creates a true back-and-forth where the human makes the final call on each remediation decision.

**If user accepts recommendation:**
```bash
curl -s -X POST "http://localhost:8080/api/documents/{JOB_ID}/checklist/{ITEM_ID}/review" \
  -H "X-API-Key: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"selected_option_id\":\"{OPTION_ID}\",\"reviewed_by\":\"interactive-review\"}"
```

**If providing custom input:**
```bash
curl -s -X POST "http://localhost:8080/api/documents/{JOB_ID}/checklist/{ITEM_ID}/review" \
  -H "X-API-Key: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"selected_option_id\":\"custom\",\"custom_input\":\"{CUSTOM_TEXT}\",\"reviewed_by\":\"interactive-review\"}"
```

**Report after each item:**
`[{n}/{total}] {question_short}... {decision}`

---

## Phase 4: Apply Reviews

After all items reviewed:

```bash
curl -s -X POST "http://localhost:8080/api/documents/{JOB_ID}/apply-reviews" \
  -H "X-API-Key: {API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{}"
```

**Report:**
```
════════════════════════════════════════════════════════════
  REVIEW SESSION COMPLETE
════════════════════════════════════════════════════════════

  Reviewed:    {n} items
  Accepted:    {accepted} (agent recommendations)
  Changed:     {changed} (your decisions)
  Skipped:     {skipped}

  Corrections: {corrections_applied}

════════════════════════════════════════════════════════════
```

---

## Phase 5: Download Result

```bash
curl -s -H "X-API-Key: {API_KEY}" "http://localhost:8080/api/documents/{JOB_ID}/result" | jq -r '.markdown' > ~/Downloads/reviewed_result.md
open ~/Downloads/reviewed_result.md
```

---

## Example Session

```
════════════════════════════════════════════════════════════
  INTERACTIVE REVIEW SESSION
════════════════════════════════════════════════════════════
  Job: 77e95ccd-efac-49d4-a636-a8dbf7c3dab9
  Items: 10 (typography)
  PDF: Opened in editor
════════════════════════════════════════════════════════════

────────────────────────────────────────────────────────────
  ITEM 1/10 | typography | Confidence: 92%
────────────────────────────────────────────────────────────

QUESTION:
  Should 'the empowerment of the community...' be formatted as italic?

CONTEXT (from page 1):
  "we have found a somewhat surprising result: the empowerment
   of the community does not come at the expense of individual success"

AGENT REASONING:
  Text appears in italics in the image for emphasis within the
  discussion of key community benefits.

OPTIONS:
  [1] Yes, format as italic ✓ recommended
  [2] No, keep as plain text

────────────────────────────────────────────────────────────

Looking at the PDF... this is indeed emphasized text in the original.
The agent's recommendation is correct.

[1/10] 'the empowerment...' → Accepted (italic)

... continues for remaining items ...

════════════════════════════════════════════════════════════
  REVIEW SESSION COMPLETE
════════════════════════════════════════════════════════════

  Reviewed:    10 items
  Accepted:    9 (agent recommendations)
  Changed:     1 (your decisions)
  Skipped:     0

  Corrections: 10

════════════════════════════════════════════════════════════
```

---

## Tips

- **High confidence (>90%)**: Agent is usually right, quick accept
- **Medium confidence (70-90%)**: Worth checking the PDF
- **Low confidence (<70%)**: Definitely check the PDF, agent is uncertain
- **Typography items**: Check if text is visually emphasized in PDF
- **Figure items**: Verify alt text matches what's shown
- **Table items**: Check structure matches original layout

---

## Quick Reference

```bash
# Start review session for a completed job
/e2e/review --job=77e95ccd-efac-49d4-a636-a8dbf7c3dab9

# Focus on just figure descriptions
/e2e/review --job=UUID --focus=figures

# Auto-accept high-confidence items
/e2e/review --job=UUID --skip-agreed
```
