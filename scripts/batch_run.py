#!/usr/bin/env python3
"""Batch-submit PDFs to a running Equalify Reflow API and collect results.

Two input modes:

    --manifest PATH   Read PDF paths from a manifest file (one path per line,
                      `#` introduces a comment). Paths are resolved relative
                      to the manifest file's parent directory. Use this for
                      reproducible benchmark corpora.

    --pdf-dir PATH    Glob `*.pdf` from a directory. Use this for ad-hoc runs
                      over a local fixture set.

Outputs a timestamped directory under `batch-results/` (or `--output`) with
per-document `metadata.json`, `result.md`, and `figures/`, plus an aggregate
`summary.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_API_URL = os.environ.get("BATCH_API_URL", "http://localhost:8080")
DEFAULT_API_KEY = os.environ.get("BATCH_API_KEY", "")
DEFAULT_CONCURRENCY = int(os.environ.get("BATCH_CONCURRENCY", "2"))

POLL_INTERVAL = 10  # seconds
SUBMIT_TIMEOUT = 120  # seconds for upload
POLL_TIMEOUT = 900  # max wait per job (15min accommodates GPU cold-start)
AUTO_APPROVE_PII = True
SUBMIT_DELAY = 5  # seconds between submissions to avoid rate limiting
MAX_RETRIES = 5
RETRY_BASE_DELAY = 10

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_BASE = REPO_ROOT / "batch-results"


# ---------------------------------------------------------------------------
# Manifest + discovery
# ---------------------------------------------------------------------------


def read_manifest(manifest: Path) -> list[Path]:
    """Parse a manifest file into a list of absolute PDF paths.

    Paths in the manifest are resolved relative to the manifest's parent dir.
    Lines starting with `#` and blank lines are ignored. An inline `  # ...`
    comment on a path line is also stripped.
    """
    if not manifest.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest}")

    base = manifest.parent
    paths: list[Path] = []
    for raw in manifest.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        p = (base / line).resolve() if not Path(line).is_absolute() else Path(line)
        paths.append(p)
    return paths


def discover_pdfs(pdf_dir: Path) -> list[Path]:
    if not pdf_dir.is_dir():
        raise NotADirectoryError(f"PDF directory not found: {pdf_dir}")
    return sorted(pdf_dir.glob("*.pdf"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def short_name(path: Path) -> str:
    stem = path.stem
    return stem[:30] if len(stem) > 30 else stem


def mb(path: Path) -> str:
    return f"{path.stat().st_size / 1_048_576:.1f} MB"


# Rate-limit gate: only one submission at a time with delay between
_submit_lock = asyncio.Lock()


async def throttled_submit(
    client: httpx.AsyncClient,
    pdf: Path,
    label: str,
    api_url: str,
    api_key: str,
) -> httpx.Response:
    """Submit with retry + backoff for 429/5xx, and inter-submission delay."""
    last_error: str | None = None
    resp: httpx.Response | None = None
    for attempt in range(MAX_RETRIES + 1):
        async with _submit_lock:
            if attempt > 0:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f"  [{label}] Retry {attempt}/{MAX_RETRIES} after {delay}s...")
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(SUBMIT_DELAY)

            with open(pdf, "rb") as f:
                resp = await client.post(
                    f"{api_url}/api/v1/documents/submit",
                    headers={"X-API-Key": api_key},
                    files={"file": (pdf.name, f, "application/pdf")},
                    timeout=SUBMIT_TIMEOUT,
                )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", RETRY_BASE_DELAY))
            print(f"  [{label}] Rate limited (429), waiting {retry_after}s...")
            await asyncio.sleep(retry_after)
            last_error = "429 Too Many Requests"
            continue
        if resp.status_code >= 500:
            last_error = f"{resp.status_code} Server Error"
            continue
        if resp.status_code == 413:
            resp.raise_for_status()

        resp.raise_for_status()
        return resp

    assert resp is not None
    raise httpx.HTTPStatusError(
        f"Failed after {MAX_RETRIES} retries: {last_error}",
        request=resp.request,
        response=resp,
    )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


async def submit_and_process(
    client: httpx.AsyncClient,
    pdf: Path,
    idx: int,
    total: int,
    sem: asyncio.Semaphore,
    results_dir: Path,
    api_url: str,
    api_key: str,
) -> dict:
    label = short_name(pdf)
    async with sem:
        print(f"[{idx}/{total}] Submitting: {label} ({mb(pdf)})", flush=True)
        t0 = time.monotonic()

        try:
            resp = await throttled_submit(client, pdf, label, api_url, api_key)
            data = resp.json()
            job_id = data["job_id"]
            print(f"  [{label}] Job ID: {job_id}", flush=True)
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  [{label}] SUBMIT ERROR: {e}", flush=True)
            return {"file": pdf.name, "status": "submit_error", "error": str(e), "elapsed": elapsed}

        deadline = time.monotonic() + POLL_TIMEOUT
        consecutive_errors = 0
        last_phase: str | None = None
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                resp = await client.get(
                    f"{api_url}/api/v1/documents/{job_id}",
                    headers={"X-API-Key": api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                consecutive_errors = 0
                data = resp.json()
                status = data.get("status", "unknown")

                if status == "processing":
                    phase = data.get("processing_phase", "")
                    jobs_done = data.get("jobs_complete", 0)
                    jobs_total = data.get("jobs_total", 0)
                    progress = f"{phase} ({jobs_done}/{jobs_total})" if jobs_total else phase
                    if progress != last_phase:
                        elapsed_so_far = time.monotonic() - t0
                        print(f"  [{label}] {elapsed_so_far:.0f}s — {progress}", flush=True)
                        last_phase = progress
                elif status != last_phase:
                    print(f"  [{label}] status: {status}", flush=True)
                    last_phase = status

                if status == "awaiting_approval" and AUTO_APPROVE_PII:
                    token = data.get("approval_token", "")
                    if token:
                        try:
                            approve_resp = await client.post(
                                f"{api_url}/api/v1/approval/{token}/decision",
                                headers={
                                    "X-API-Key": api_key,
                                    "Content-Type": "application/json",
                                },
                                json={"decision": "approved", "reviewed_by": "batch_run"},
                                timeout=30,
                            )
                            approve_resp.raise_for_status()
                            pii_count = len(data.get("pii_findings", []))
                            print(f"  [{label}] Auto-approved {pii_count} PII findings", flush=True)
                        except Exception as e:
                            print(f"  [{label}] PII approve error: {e}", flush=True)
                    continue

                if status == "completed":
                    elapsed = time.monotonic() - t0
                    pages = data.get("total_pages", "?")
                    cost = data.get("llm_cost", {}).get("estimated_cost_dollars", 0)
                    print(f"  [{label}] OK in {elapsed / 60:.1f}m — {pages}p, ${cost:.4f}", flush=True)
                    await save_result(client, data, results_dir / label)
                    return {
                        "file": pdf.name,
                        "status": "completed",
                        "job_id": job_id,
                        "pages": pages,
                        "cost": cost,
                        "tokens": data.get("llm_cost", {}).get("total_tokens", 0),
                        "elapsed": elapsed,
                        "edits": data.get("total_edits", 0),
                    }

                if status == "failed":
                    elapsed = time.monotonic() - t0
                    error = data.get("error", "unknown")
                    print(f"  [{label}] FAIL in {elapsed / 60:.1f}m", flush=True)
                    return {
                        "file": pdf.name,
                        "status": "failed",
                        "job_id": job_id,
                        "error": error,
                        "elapsed": elapsed,
                    }

            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= 5:
                    print(f"  [{label}] Too many poll errors, giving up: {e}", flush=True)
                    elapsed = time.monotonic() - t0
                    return {"file": pdf.name, "status": "error", "job_id": job_id, "error": str(e), "elapsed": elapsed}
                print(f"  [{label}] Poll error ({consecutive_errors}/5): {e}", flush=True)

        elapsed = time.monotonic() - t0
        print(f"  [{label}] TIMEOUT after {elapsed / 60:.1f}m", flush=True)
        return {"file": pdf.name, "status": "timeout", "job_id": job_id, "elapsed": elapsed}


async def save_result(client: httpx.AsyncClient, data: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "metadata.json").write_text(json.dumps(data, indent=2, default=str))

    md_url = data.get("markdown_url", "")
    if md_url:
        try:
            resp = await client.get(md_url, timeout=30)
            resp.raise_for_status()
            (out_dir / "result.md").write_bytes(resp.content)
        except Exception:
            pass

    figures = data.get("figures", [])
    if figures:
        fig_dir = out_dir / "figures"
        fig_dir.mkdir(exist_ok=True)
        for fig in figures:
            url = fig.get("url", "")
            fid = fig.get("figure_id", "figure")
            if url:
                try:
                    resp = await client.get(url, timeout=30)
                    resp.raise_for_status()
                    (fig_dir / f"{fid}.png").write_bytes(resp.content)
                except Exception:
                    pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", type=Path, help="Path to a manifest file listing PDFs to process.")
    src.add_argument("--pdf-dir", type=Path, help="Directory to glob *.pdf from.")
    p.add_argument("--output", type=Path, default=None, help="Output directory (default: batch-results/<timestamp>).")
    p.add_argument("--api-url", default=DEFAULT_API_URL, help=f"API base URL (default: {DEFAULT_API_URL}).")
    p.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Concurrent jobs (default: {DEFAULT_CONCURRENCY}).",
    )
    return p.parse_args()


async def main() -> None:
    args = parse_args()

    api_key = DEFAULT_API_KEY
    if not api_key:
        print("Error: Set BATCH_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    if args.manifest:
        pdfs = read_manifest(args.manifest)
    else:
        pdfs = discover_pdfs(args.pdf_dir)

    missing = [p for p in pdfs if not p.is_file()]
    if missing:
        print(f"WARNING: {len(missing)} PDFs not found:", file=sys.stderr)
        for p in missing:
            print(f"  - {p}", file=sys.stderr)
    pdfs = [p for p in pdfs if p.is_file()]

    if not pdfs:
        print("No PDFs to process.", file=sys.stderr)
        sys.exit(1)

    # Sort by size ascending to avoid overloading docling with a huge doc first
    pdfs.sort(key=lambda p: p.stat().st_size)

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
    results_dir = args.output or (DEFAULT_RESULTS_BASE / stamp)
    results_dir.mkdir(parents=True, exist_ok=True)

    total = len(pdfs)
    print(f"Batch run: {total} PDFs, concurrency={args.concurrency}")
    print(f"API: {args.api_url}")
    print(f"Output: {results_dir}\n", flush=True)

    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        tasks = [
            submit_and_process(client, pdf, i + 1, total, sem, results_dir, args.api_url, api_key)
            for i, pdf in enumerate(pdfs)
        ]
        results = await asyncio.gather(*tasks)

    completed = [r for r in results if r["status"] == "completed"]
    failed = [r for r in results if r["status"] != "completed"]

    total_cost = sum(r.get("cost", 0) for r in completed)
    total_tokens = sum(r.get("tokens", 0) for r in completed)
    elapsed_list = [r["elapsed"] for r in results if "elapsed" in r]

    print()
    print("=" * 70)
    print("BATCH PROCESSING SUMMARY")
    print("=" * 70)
    print(f"  Documents: {total}")
    print(f"  Completed: {len(completed)} | Failed: {len(failed)}")
    print(f"  Success rate: {len(completed) / total * 100:.1f}%")
    print(f"  Total cost: ${total_cost:.4f}")
    print(f"  Total tokens: {total_tokens:,}")

    if elapsed_list:
        print(
            f"  Processing time — mean: {statistics.mean(elapsed_list):.0f}s, "
            f"median: {statistics.median(elapsed_list):.0f}s, "
            f"p95: {sorted(elapsed_list)[int(len(elapsed_list) * 0.95)]:.0f}s"
        )

    if completed:
        costs_per_page = [
            r["cost"] / r["pages"] for r in completed if isinstance(r.get("pages"), int) and r["pages"] > 0
        ]
        if costs_per_page:
            print(
                f"  Cost/page — mean: ${statistics.mean(costs_per_page):.4f}, "
                f"median: ${statistics.median(costs_per_page):.4f}"
            )

    if failed:
        print("\n  Failures:")
        for r in failed:
            print(f"    - {r['file']}: {r.get('error', '')}")

    print(f"\n  Results saved to: {results_dir}")

    summary = {
        "timestamp": stamp,
        "api_url": args.api_url,
        "concurrency": args.concurrency,
        "total": total,
        "completed": len(completed),
        "failed": len(failed),
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "results": results,
    }
    (results_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
