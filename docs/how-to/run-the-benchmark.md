# Run the benchmark

Equalify Reflow ships with a reproducible benchmark harness: `scripts/batch_run.py` submits a corpus of PDFs to a running API, polls each job to completion (auto-approving PII findings), and writes per-document results plus an aggregate summary to disk. The published benchmark corpora live under [`docs/reference/benchmarks/`](../reference/benchmarks/).

Use this to:

- Reproduce a published pilot against the current pipeline
- Regression-check pipeline changes before a release
- Compare two releases against the same corpus (before/after)
- Build a fixture set from an ad-hoc directory of PDFs

## Prerequisites

- A running Equalify Reflow API. Use `make dev` locally, or point at a deployed instance (staging, production).
- An API key with submit access, exported as `BATCH_API_KEY`.
- The PDF corpus on local disk (see [Reproducing a published corpus](#reproducing-a-published-corpus) below for why PDFs are not checked in).

## Two input modes

### Manifest mode — reproducible corpora

Manifests are plain-text files listing one PDF path per line (comments start with `#`, paths resolve relative to the manifest). This is the mode to use for any published benchmark.

```bash
uv run scripts/batch_run.py \
    --manifest docs/reference/benchmarks/v0.1.0-beta.6-pilot/manifest.txt
```

### Directory mode — ad-hoc runs

For one-off regression checks against a local folder of PDFs:

```bash
uv run scripts/batch_run.py --pdf-dir ~/some-pdfs/
```

## Common flags

| Flag | Purpose | Default |
|---|---|---|
| `--manifest PATH` | Read PDF list from a manifest file | — |
| `--pdf-dir PATH` | Glob `*.pdf` from a directory | — |
| `--output PATH` | Where to write results | `batch-results/<UTC timestamp>` |
| `--api-url URL` | API base URL | `$BATCH_API_URL` or `http://localhost:8080` |
| `--concurrency N` | Parallel jobs | `$BATCH_CONCURRENCY` or `2` |

`BATCH_API_KEY` is required and read only from the environment — never from a flag — so keys don't end up in shell history.

## What you get back

```
<output-dir>/
├── summary.json                 # aggregate: counts, total cost, timings, per-doc results
├── <doc-label>/
│   ├── metadata.json            # full pipeline metadata (job ID, phases, ledger, llm_cost)
│   ├── result.md                # converted markdown
│   └── figures/
│       └── figure-1.png, ...    # extracted figures
```

`summary.json` is the canonical benchmark artifact: total docs, completed, failed, total cost/tokens, and a `results[]` array with per-document `cost`, `tokens`, `elapsed`, `pages`, and `edits`.

## Reproducing a published corpus

Published corpora (e.g. `v0.1.0-beta.6-pilot/`) ship the manifest but not the PDFs. Several documents in the UIC pilot corpus are third-party copyrighted works (book chapters, newsletters), so redistributing them in a public repo would be inappropriate.

To reproduce a published run:

1. Obtain the corpus separately (UIC team members: ask in the project channel; external reproducers: substitute your own documents matching the type/page-count profile in the manifest).
2. Place the PDFs under `samples/` beside the manifest, using the filenames listed in the manifest.
3. Run `batch_run.py --manifest <path>` as above.
4. Compare `summary.json` against the published one. Differences are expected when model versions or prompts have changed — that's the point.

## Tips

- Start small. Run against a 2–3 PDF manifest before a 30-PDF one to catch auth / connectivity issues early.
- Watch rate limits. The script throttles submissions (5s delay, retries with backoff) but a busy shared API will still 429. Lower `--concurrency` if you see sustained 429s.
- GPU cold starts. First submission after an idle period can take minutes; the per-job timeout is 15 min for this reason.
- Costs are real. Each run against a production instance incurs Bedrock (or Anthropic) token charges. The pilot's 30 docs cost ~$13 — plan accordingly.

## Related

- [`docs/reference/benchmarks/`](../reference/benchmarks/) — published corpora and reports
- [`docs/how-to/iterate-on-a-prompt.md`](iterate-on-a-prompt.md) — for targeted prompt-change validation against a single document
- [`docs/how-to/run-tests.md`](run-tests.md) — for the unit / integration / e2e test suite (different purpose: code correctness, not conversion quality)
