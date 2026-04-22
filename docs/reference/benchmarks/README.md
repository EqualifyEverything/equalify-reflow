# Benchmarks

Published benchmark runs for Equalify Reflow. Each pilot is a self-contained folder — manifest, raw aggregates, per-document review notes, and a report in `README.md`.

## Pilots

| Release | Corpus | Docs | Pages | Report |
|---|---|---|---|---|
| `v0.1.0-beta.6` | UIC diverse sample (policy, academic, event, infographic, syllabus) | 30 | 174 | [`v0.1.0-beta.6-pilot/`](v0.1.0-beta.6-pilot/) |

## Reproducing a run

Every pilot ships a manifest you can hand to `scripts/batch_run.py`. See [`docs/how-to/run-the-benchmark.md`](../../how-to/run-the-benchmark.md) for the full workflow.

```bash
BATCH_API_KEY=... uv run scripts/batch_run.py \
    --manifest docs/reference/benchmarks/v0.1.0-beta.6-pilot/manifest.txt
```

## Adding a new pilot

1. Create `docs/reference/benchmarks/<release-tag>-pilot/`.
2. Commit `manifest.txt` (paths, not PDFs — source files are curator-distributed).
3. Run the benchmark, then commit `summary.json`, `per-document-scores.csv`, and `notes/`.
4. Write `README.md` following the structure of the previous pilot.
5. Add a row to the table above.
