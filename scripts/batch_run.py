#!/usr/bin/env python3
"""Batch-submit PDFs and collect results."""

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_URL = os.environ.get("BATCH_API_URL", "http://localhost:8080")
API_KEY = os.environ.get("BATCH_API_KEY", "")
CONCURRENCY = int(os.environ.get("BATCH_CONCURRENCY", "2"))
POLL_INTERVAL = 10  # seconds
SUBMIT_TIMEOUT = 120  # seconds for upload
POLL_TIMEOUT = 900  # seconds max wait per job (15min for GPU cold-start)
AUTO_APPROVE_PII = True  # auto-approve PII findings
SUBMIT_DELAY = 5  # seconds between submissions to avoid rate limiting
MAX_RETRIES = 5  # max retries for 429/5xx on submit
RETRY_BASE_DELAY = 10  # base delay for exponential backoff (seconds)

PDF_DIRS = [
    Path(__file__).resolve().parent.parent
    / "project-docs"
    / "UIC Documents"
    / "downloaded-samples"
    / "diverse-sample-pdfs",
]
EXTRA_PDFS = [
    Path(__file__).resolve().parent.parent
    / "project-docs"
    / "pdfs"
    / "Undergraduate Course Syllabi (Field_ intro_text_body)_ad82e678.pdf",
]

RESULTS_BASE = Path(__file__).resolve().parent.parent / "batch-results"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def short_name(path: Path) -> str:
    stem = path.stem
    if len(stem) > 30:
        stem = stem[:30]
    return stem


def mb(path: Path) -> str:
    return f"{path.stat().st_size / 1_048_576:.1f} MB"


# Rate-limit gate: only one submission at a time with delay between
_submit_lock = asyncio.Lock()


async def throttled_submit(
    client: httpx.AsyncClient,
    pdf: Path,
    label: str,
) -> httpx.Response:
    """Submit with retry + backoff for 429/5xx, and inter-submission delay."""
    last_error = None
    for attempt in range(MAX_RETRIES + 1):
        async with _submit_lock:
            if attempt > 0:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(f"  [{label}] Retry {attempt}/{MAX_RETRIES} after {delay}s...")
                await asyncio.sleep(delay)
            else:
                # Small delay between submissions even on first attempt
                await asyncio.sleep(SUBMIT_DELAY)

            with open(pdf, "rb") as f:
                resp = await client.post(
                    f"{API_URL}/api/v1/documents/submit",
                    headers={"X-API-Key": API_KEY},
                    files={"file": (pdf.name, f, "application/pdf")},
                    timeout=SUBMIT_TIMEOUT,
                )

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", RETRY_BASE_DELAY))
            print(f"  [{label}] Rate limited (429), waiting {retry_after}s...")
            await asyncio.sleep(retry_after)
            last_error = f"429 Too Many Requests"
            continue
        elif resp.status_code >= 500:
            last_error = f"{resp.status_code} Server Error"
            continue
        elif resp.status_code == 413:
            resp.raise_for_status()  # Too large, no point retrying

        resp.raise_for_status()
        return resp

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
) -> dict:
    label = short_name(pdf)
    async with sem:
        print(f"[{idx}/{total}] Submitting: {label} ({mb(pdf)})")
        t0 = time.monotonic()

        # Submit with retry
        try:
            resp = await throttled_submit(client, pdf, label)
            data = resp.json()
            job_id = data["job_id"]
            print(f"  [{label}] Job ID: {job_id}")
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  [{label}] SUBMIT ERROR: {e}")
            return {"file": pdf.name, "status": "submit_error", "error": str(e), "elapsed": elapsed}

        # Poll
        deadline = time.monotonic() + POLL_TIMEOUT
        consecutive_errors = 0
        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            try:
                resp = await client.get(
                    f"{API_URL}/api/v1/documents/{job_id}",
                    headers={"X-API-Key": API_KEY},
                    timeout=30,
                )
                resp.raise_for_status()
                consecutive_errors = 0
                data = resp.json()
                status = data.get("status", "unknown")

                # Auto-approve PII
                if status == "awaiting_approval" and AUTO_APPROVE_PII:
                    token = data.get("approval_token", "")
                    if token:
                        try:
                            approve_resp = await client.post(
                                f"{API_URL}/api/v1/approval/{token}/decision",
                                headers={
                                    "X-API-Key": API_KEY,
                                    "Content-Type": "application/json",
                                },
                                json={"decision": "approved", "reviewed_by": "batch_run"},
                                timeout=30,
                            )
                            approve_resp.raise_for_status()
                            pii_count = len(data.get("pii_findings", []))
                            print(f"  [{label}] Auto-approved {pii_count} PII findings")
                        except Exception as e:
                            print(f"  [{label}] PII approve error: {e}")
                    continue

                if status == "completed":
                    elapsed = time.monotonic() - t0
                    pages = data.get("total_pages", "?")
                    cost = data.get("llm_cost", {}).get("estimated_cost_dollars", 0)
                    print(f"  [{label}] OK in {elapsed / 60:.1f}m — {pages}p, ${cost:.4f}")
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
                    print(f"  [{label}] FAIL in {elapsed / 60:.1f}m — ?p, $0.0000")
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
                    print(f"  [{label}] Too many poll errors, giving up: {e}")
                    elapsed = time.monotonic() - t0
                    return {"file": pdf.name, "status": "error", "job_id": job_id, "error": str(e), "elapsed": elapsed}
                print(f"  [{label}] Poll error ({consecutive_errors}/5): {e}")

        elapsed = time.monotonic() - t0
        print(f"  [{label}] TIMEOUT after {elapsed / 60:.1f}m")
        return {"file": pdf.name, "status": "timeout", "job_id": job_id, "elapsed": elapsed}


async def save_result(client: httpx.AsyncClient, data: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save metadata
    (out_dir / "metadata.json").write_text(json.dumps(data, indent=2, default=str))

    # Download markdown
    md_url = data.get("markdown_url", "")
    if md_url:
        try:
            resp = await client.get(md_url, timeout=30)
            resp.raise_for_status()
            (out_dir / "result.md").write_bytes(resp.content)
        except Exception:
            pass

    # Download figures
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
                    ext = ".png"
                    (fig_dir / f"{fid}{ext}").write_bytes(resp.content)
                except Exception:
                    pass


async def main():
    if not API_KEY:
        print("Error: Set BATCH_API_KEY environment variable")
        sys.exit(1)

    # Collect PDFs
    pdfs = []
    for d in PDF_DIRS:
        if d.exists():
            pdfs.extend(sorted(d.glob("*.pdf")))
    for p in EXTRA_PDFS:
        if p.exists():
            pdfs.append(p)

    if not pdfs:
        print("No PDFs found")
        sys.exit(1)

    # Sort by size ascending (small ones first — avoids overloading docling early)
    pdfs.sort(key=lambda p: p.stat().st_size)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    results_dir = RESULTS_BASE / stamp
    results_dir.mkdir(parents=True, exist_ok=True)

    total = len(pdfs)
    print(f"Batch run: {total} PDFs, concurrency={CONCURRENCY}")
    print(f"API: {API_URL}")
    print(f"Output: {results_dir}")
    print()

    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient() as client:
        tasks = [
            submit_and_process(client, pdf, i + 1, total, sem, results_dir)
            for i, pdf in enumerate(pdfs)
        ]
        results = await asyncio.gather(*tasks)

    # Summary
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
            r["cost"] / r["pages"]
            for r in completed
            if isinstance(r.get("pages"), int) and r["pages"] > 0
        ]
        if costs_per_page:
            print(
                f"  Cost/page — mean: ${statistics.mean(costs_per_page):.4f}, "
                f"median: ${statistics.median(costs_per_page):.4f}"
            )

    if failed:
        print()
        print("  Failures:")
        for r in failed:
            error = r.get("error", "")
            print(f"    - {r['file']}: {error}")

    print(f"\n  Results saved to: {results_dir}")

    # Save summary
    summary = {
        "timestamp": stamp,
        "api_url": API_URL,
        "concurrency": CONCURRENCY,
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
