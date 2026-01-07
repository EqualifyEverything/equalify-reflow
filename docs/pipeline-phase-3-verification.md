# V5 Phase 3: Verification

Phase 3 performs quality checks on the processed document before declaring success.

**Goal:** Verify document meets accessibility and quality standards.

**Input:** Final markdowns + DocumentPlan + Ledger

**Output:** `VerificationReport` (pass/fail + issues)

---

## Overview

Verification runs 5 layers of checks:

```
Layer 1: Per-Page Basic Checks
Layer 2: Plan-Based Verification (V3.1-V3.4)
Layer 3: Cross-Page Consistency
Layer 4: Ledger Analysis
Layer 5: Overall Assessment
    ↓
VerificationReport (pass/fail)
    ↓
If failed + >= 50% pages passed → Recovery Phase
If failed + < 50% pages passed → Mark as FAILED
If passed → Mark as COMPLETE
```

**Location:** `src/agents/v5/plan_verification.py`, `src/agents/v5/orchestrator.py`

---

## Layer 1: Per-Page Basic Checks

**Function:** `_verify_page_basics()` in `orchestrator.py:114-164`

### Checks

For each page markdown:

#### 1. Unfilled Placeholders

```python
# Check for un-processed placeholders
unfilled = re.findall(r'<!--\s*(image|table)\s*\d*\s*-->', markdown, re.IGNORECASE)

if unfilled:
    issues.append(f"Unfilled placeholders: {', '.join(unfilled)}")
    critical = True
```

**Critical:** YES (accessibility blocker)

#### 2. Empty Alt-Text

```python
# Check for images without alt-text
empty_alt = re.findall(r'!\[\]\([^)]+\)', markdown)

if empty_alt:
    issues.append(f"{len(empty_alt)} images with empty alt-text")
    # Not critical if decorative
```

**Critical:** NO (may be decorative)

#### 3. Heading Hierarchy

```python
# Extract heading levels
headings = re.findall(r'^(#{1,6})\s+(.+)$', markdown, re.MULTILINE)
levels = [len(h[0]) for h in headings]

# Check for skips (e.g., H2 → H4)
for i in range(1, len(levels)):
    if levels[i] - levels[i-1] > 1:
        issues.append(f"Heading hierarchy skip: H{levels[i-1]} → H{levels[i]}")
        critical = True
```

**Critical:** YES (structure violation)

### Output

```python
PageVerification(
    page_num=3,
    passed=False,
    issues=[
        "Unfilled placeholder: <!-- image 1 -->",
        "Heading hierarchy skip: H2 → H4"
    ],
    confidence=0.9
)
```

---

## Layer 2: Plan-Based Verification

**Function:** `verify_document()` in `orchestrator.py:175-217`

### V3.1: Heading Structure

**Function:** `verify_heading_structure()` in `plan_verification.py`

**Purpose:** Verify all outline headings exist at correct levels

```python
def verify_heading_structure(final_markdowns, plan):
    issues = []

    # Extract all headings from final markdown
    all_headings = extract_all_headings(final_markdowns)

    # Check each outline entry exists
    for entry in flatten_outline(plan.structure.outline):
        matching_heading = find_heading(all_headings, entry.heading)

        if not matching_heading:
            issues.append(f"Missing heading: '{entry.heading}' (expected on page {entry.page_start})")

        elif matching_heading.level != entry.level:
            issues.append(f"Wrong level: '{entry.heading}' is H{matching_heading.level}, expected H{entry.level}")

    return issues
```

**Critical Issues:**
- Missing required headings
- Wrong heading levels (violates hierarchy)

---

### V3.2: Figure Completeness

**Function:** `verify_figure_completeness()` in `plan_verification.py`

**Purpose:** Verify all planned figures have alt-text

```python
def verify_figure_completeness(final_markdowns, plan):
    issues = []

    for page_num, page_plan in plan.pages.items():
        markdown = final_markdowns[page_num]

        for figure in page_plan.figures:
            # Skip decorative figures
            if figure.is_decorative:
                continue

            # Check if figure has alt-text
            # Look for ![...](imageN.png) or ![...](<!-- image N -->)
            pattern = rf'!\[[^\]]+\]\([^)]*image{figure.figure_index}[^)]*\)'
            match = re.search(pattern, markdown, re.IGNORECASE)

            if not match:
                issues.append(f"Page {page_num}: Figure {figure.figure_index} missing alt-text")

    return issues
```

**Critical:** YES (accessibility requirement)

---

### V3.3: Table Completeness

**Function:** `verify_table_completeness()` in `plan_verification.py`

**Purpose:** Verify all planned tables are transcribed

```python
def verify_table_completeness(final_markdowns, plan):
    issues = []

    for page_num, page_plan in plan.pages.items():
        markdown = final_markdowns[page_num]

        for table in page_plan.tables:
            # Check if table transcribed (has pipes)
            # Look for markdown table near where table should be
            has_table = bool(re.search(r'\|.*\|', markdown))

            # Also check no placeholder remains
            placeholder_pattern = rf'<!--\s*table\s*{table.table_index}\s*-->'
            has_placeholder = bool(re.search(placeholder_pattern, markdown, re.IGNORECASE))

            if has_placeholder or not has_table:
                issues.append(f"Page {page_num}: Table {table.table_index} not transcribed")

    return issues
```

**Critical:** YES (accessibility requirement)

---

### V3.4: Spelling Verification

**Function:** `verify_spelling()` in `plan_verification.py`

**Purpose:** Check for spelling errors using document dictionary

```python
def verify_spelling(final_markdowns, dictionary):
    issues = []
    spell = SpellChecker()
    spell.word_frequency.load_words(dictionary)

    for page_num, markdown in final_markdowns.items():
        # Extract words
        words = re.findall(r'\b[a-zA-Z]{3,}\b', markdown)

        misspelled = []
        for word in words:
            if word.lower() not in spell and word not in dictionary:
                misspelled.append(word)

        if misspelled:
            # Limit to first 10 to avoid noise
            sample = misspelled[:10]
            issues.append(f"Page {page_num}: Possible spelling errors: {', '.join(sample)}")

    return issues
```

**Critical:** NO (informational only, too many false positives)

---

## Layer 3: Cross-Page Consistency

### Check 1: Duplicate Headings

```python
# Ensure no duplicate H1 headings
h1_headings = [h for h in all_headings if h.level == 1]
if len(h1_headings) > 1:
    issues.append(f"Multiple H1 headings: {[h.text for h in h1_headings]}")
```

### Check 2: Page Breaks

```python
# Check for proper page boundaries
# (Optional - depends on pageless optimization flag)
if not optimized:
    # Verify page separators exist
    separators = re.findall(r'---\nPage \d+\n---', full_markdown)
    if len(separators) != total_pages - 1:
        warnings.append("Inconsistent page separators")
```

---

## Layer 4: Ledger Analysis

### Check: Validation Rate

```python
validated_edits = [e for e in ledger.entries if e.validated]
total_edits = len(ledger.entries)

validation_rate = len(validated_edits) / total_edits if total_edits > 0 else 0

if validation_rate < 0.8:
    warnings.append(f"Low validation rate: {validation_rate:.1%} ({total_edits - len(validated_edits)} rejected)")
```

### Check: Confidence Distribution

```python
low_confidence_edits = [e for e in ledger.entries if e.confidence < 0.7]

if len(low_confidence_edits) > total_edits * 0.2:
    warnings.append(f"{len(low_confidence_edits)} low-confidence edits (< 0.7)")
```

---

## Layer 5: Overall Assessment

### Aggregation

```python
# Collect all issues
all_issues = []
all_issues.extend(layer1_issues)  # Per-page basics
all_issues.extend(layer2_issues)  # Plan-based
all_issues.extend(layer3_issues)  # Cross-page
all_issues.extend(layer4_warnings)  # Ledger

# Classify
critical_issues = [i for i in all_issues if is_critical(i)]
warnings = [i for i in all_issues if not is_critical(i)]
```

### Pass/Fail Decision

```python
# Count pages that passed basic checks
pages_passed = sum(1 for pv in page_verifications if pv.passed)
pages_failed = len(page_verifications) - pages_passed

# Overall pass criteria:
# 1. NO critical issues
# 2. >= 80% pages passed basic checks

overall_passed = (
    len(critical_issues) == 0
    and pages_passed >= len(page_verifications) * 0.8
)
```

### Report Assembly

```python
report = VerificationReport(
    document_id=document_id,
    passed=overall_passed,
    pages=page_verifications,
    total_issues=len(all_issues),
    critical_issues=critical_issues,
    warnings=warnings,
    pages_passed=pages_passed,
    pages_failed=pages_failed,
    verification_duration_ms=duration
)
```

---

## Events Emitted

- `VerificationStartedEvent` - When verification begins
- `PageVerifiedEvent` - For each page (page_num, passed, issue_count)
- `VerificationCompleteEvent` - With summary (passed, total_issues, critical_count)

---

## Recovery Trigger Logic

```python
if not report.passed:
    # Trigger recovery if >= 50% pages passed
    if report.pages_passed >= len(report.pages) * 0.5:
        # Attempt recovery on failed pages
        recovery_report = await run_recovery_phase(...)
    else:
        # Too many failures, skip recovery
        final_status = ProcessingStatus.FAILED
```

**Rationale:**
- If >= 50% pages good, failures likely fixable
- If < 50% pages good, systemic issues (recovery unlikely to help)

---

## Example Verification Report

```python
VerificationReport(
    document_id="doc-123",
    passed=False,

    pages=[
        PageVerification(page_num=1, passed=True, issues=[]),
        PageVerification(page_num=2, passed=True, issues=[]),
        PageVerification(page_num=3, passed=False, issues=[
            "Unfilled placeholder: <!-- image 1 -->",
            "Heading hierarchy skip: H2 → H4"
        ]),
        ...
    ],

    total_issues=8,

    critical_issues=[
        "Page 3: Unfilled placeholder: <!-- image 1 -->",
        "Page 3: Heading hierarchy skip: H2 → H4",
        "Page 5: Figure 1 missing alt-text"
    ],

    warnings=[
        "Page 7: Possible spelling errors: neur al, proces sing",
        "Low validation rate: 85% (3 rejected edits)"
    ],

    pages_passed=7,
    pages_failed=3,
    verification_duration_ms=5000
)
```

**Outcome:** Failed, but 70% pages passed → Trigger recovery

---

## Metrics

**10-page Document:**

| Metric | Value |
|--------|-------|
| Verification duration | 3-5s |
| Checks per page | 10-15 |
| Total checks | 100-150 |
| Pass rate (typical) | 80-95% |
| Critical issues (typical) | 0-3 |
| Warnings (typical) | 2-10 |

---

## Debugging

### Enable Detailed Logging

```python
import logging
logging.getLogger("src.agents.v5.plan_verification").setLevel(logging.DEBUG)
```

### Inspect Failed Pages

```python
failed_pages = [pv for pv in report.pages if not pv.passed]
for pv in failed_pages:
    print(f"Page {pv.page_num}: {pv.issues}")
```

### Review Critical Issues

```python
for issue in report.critical_issues:
    print(f"CRITICAL: {issue}")
```

---

## Common Issues

### Issue: Too Many False Positive Spelling Errors

**Symptom:** Warnings list full of valid technical terms

**Fix:** Expand document dictionary in planning phase

---

### Issue: Heading Hierarchy False Positives

**Symptom:** Reports skips that are actually valid

**Fix:** Review outline generation logic in planning

---

### Issue: Empty Alt-Text Flagged Incorrectly

**Symptom:** Decorative images flagged as missing alt-text

**Fix:** Ensure `is_decorative=True` set correctly during planning

---

## Next Steps

- Review [Phase 4: Recovery](./pipeline-phase-4-recovery.md) for error recovery
- Check [Data Models Reference](./pipeline-data-models.md) for `VerificationReport` schema
- Explore [System Overview](./pipeline-system-overview.md) for complete pipeline
