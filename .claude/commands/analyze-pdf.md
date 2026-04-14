---
description: Submit PDF for processing and analyze agent behavior via debug bundle
argument-hint: <pdf-path> [analysis question]
allowed-tools: Read, Bash, Glob, Grep, Write
---

# PDF Debug Analysis

Submit a PDF for processing with debug bundle enabled, then analyze agent behavior.

## Arguments

- `$ARGUMENTS` - PDF path (required), optionally followed by an analysis question

## Instructions

You are analyzing PDF processing behavior using debug bundles. Follow these stages:

### Stage 1: Parse Arguments & Validate

Parse the arguments: `$ARGUMENTS`

1. First token is the PDF path (required)
2. Everything after is the optional analysis question
3. If no PDF path provided, ask the user for one

Validate the PDF exists. Use a path relative to the project root at `/Users/dylanisaac/Projects/equalify-reflow/` if not absolute.

### Stage 2: Load Configuration

Read the API key from `/Users/dylanisaac/Projects/equalify-reflow/.env`:
- Look for `API_KEYS=` line
- Extract the first key (before any comma if multiple)

Create a working directory:
```bash
mkdir -p /tmp/pdf-debug-$(date +%s)
```

Store this path for later use.

### Stage 3: Submit PDF

Submit the PDF with debug bundle enabled:

```bash
curl -s -X POST "http://localhost:8080/api/v1/documents/submit" \
  -H "X-API-Key: {API_KEY}" \
  -F "file=@{PDF_PATH}" \
  -F "skip_pii_scan=true" \
  -F "generate_debug_bundle=true" \
  -F "max_rounds=2"
```

Extract the `job_id` from the JSON response. If the API returns an error:
- Check if services are running with `docker ps | grep -E "(api|worker)"`
- Suggest running `make dev` in the project directory

Display: "Submitted job: {job_id}"

### Stage 4: Poll for Completion

Poll the job status every 15 seconds:

```bash
curl -s "http://localhost:8080/api/v1/documents/{job_id}" \
  -H "X-API-Key: {API_KEY}"
```

Display progress on each poll:
- Status: `status` field
- Phase: `processing_phase` field
- Progress: `jobs_complete` / `jobs_total`

Continue polling until:
- `status` is `completed` or `failed`
- 20 minutes elapsed (80 polls) - ask user if they want to continue

If status is `failed`, show the error and offer to display logs.

### Stage 5: Download & Extract Debug Bundle

Once completed, download and extract:

```bash
cd {WORKING_DIR}
curl -s "http://localhost:8080/api/v1/documents/{job_id}/debug-bundle" \
  -H "X-API-Key: {API_KEY}" -o bundle.zip
unzip -o bundle.zip -d ./bundle/
```

If download fails, the job may not have generated a debug bundle.

### Stage 6: Analyze Bundle

Read and analyze these key files from the bundle:

1. **manifest.json** - Job overview
   - Total tokens used
   - Total cost
   - Phases executed
   - Agents invoked

2. **phase_1_planning/*.json** - Planner decisions
   - What structure was detected per page
   - Heading hierarchy analysis
   - Planning rationale

3. **phase_2_execution/*.json** - Worker outputs
   - How each page was processed
   - Paragraph agent decisions
   - Critic feedback (if any)

4. **Job status response** - Already fetched, contains LLM cost breakdown

#### Default Analysis (if no question provided)

Answer these questions:
1. What document structure was detected? Was heading hierarchy correct?
2. Which agents were invoked and why?
3. Token/cost breakdown by agent type - any inefficiencies?
4. Were there errors, low-confidence results, or unexpected outputs?
5. What specific prompts led to key decisions?

#### Custom Question Analysis

If user provided a question: `{ANALYSIS_QUESTION}`

Focus on finding the relevant artifacts that answer this question:
- Identify which phase/agent is relevant
- Read the specific JSON files for that agent
- Show the prompts and responses that led to the behavior
- Explain what happened and why

### Debug Bundle Structure Reference

```
bundle/
├── manifest.json                 # Job metadata, costs, phases
├── input/original.pdf           # Source PDF
├── phase_1_planning/
│   ├── planner_page_1_analysis.json
│   ├── planner_page_N_analysis.json
│   └── critic_round_N_analysis.json
├── phase_2_execution/
│   ├── worker_page_N_*.json
│   └── paragraph_agent_page_N_*.json
└── README.md
```

Each artifact JSON contains:
- `prompt` - Full LLM prompt sent
- `response_raw` - Full LLM response
- `metadata.tokens` - Input/output token counts
- `metadata.cost_cents` - Cost for this call
- `metadata.model_id` - Model used
- `metadata.duration_ms` - Execution time

## Output Format

Provide a clear summary with:
1. Job submission details
2. Processing timeline
3. Cost breakdown
4. Analysis findings (answering the question or default questions)
5. Path to the extracted bundle for further investigation

## Error Handling

- **PDF not found**: Show exact path checked, ask for correct path
- **API not running**: Show docker status, suggest `make dev`
- **Job failed**: Show error from status response, offer to check worker logs
- **Timeout**: Offer to continue polling or abort
- **No debug bundle**: Explain the job wasn't submitted with debug flag (shouldn't happen with this command)
