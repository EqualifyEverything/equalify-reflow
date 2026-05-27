"""Print every Canvas platform that has launched the tool.

Phase 1 ops CLI for the multi-tenant migration. Connects to the same
Redis the api-gateway uses and dumps the platform registry as a small
table. Useful for confirming "did the launch from canvas.test.example
actually write a record?".

Usage from the host::

    docker compose exec api-gateway python -m scripts.list_platforms

Or in JSON mode for piping into jq::

    docker compose exec api-gateway python -m scripts.list_platforms --json

The output is intentionally compact; one record per row, no
pretty-printing of timestamps. The point is fast eyeballing during
diagnosis, not a polished admin UI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from redis.asyncio import Redis

from src.config import settings
from src.lti.platform_store import list_platforms


async def _main(as_json: bool) -> int:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    try:
        records = await list_platforms(redis)
    finally:
        await redis.aclose()

    if as_json:
        json.dump(
            [r.to_json() for r in records],
            sys.stdout,
            indent=2,
            default=str,
        )
        sys.stdout.write("\n")
        return 0

    if not records:
        print("No platforms registered yet.")
        print("(Records are created on the first successful LTI launch.)")
        return 0

    # Column widths chosen to fit a typical 120-column terminal without
    # wrapping while leaving room for canvas.school-name.example domains.
    header = f"{'PLATFORM_ID':<18} {'DOMAIN':<40} {'DEPLOYMENT':<18} {'LAST LAUNCH':<22} {'REV':<5}"
    print(header)
    print("-" * len(header))
    for r in sorted(records, key=lambda x: x.last_launch_at, reverse=True):
        dep = (r.deployment_id or "")[:16]
        last = (r.last_launch_at or "")[:19].replace("T", " ")
        rev = "yes" if r.revoked_at else ""
        print(f"{r.platform_id:<18} {r.canvas_domain:<40} {dep:<18} {last:<22} {rev:<5}")
    print(f"\n{len(records)} platform(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="List registered Canvas platforms.")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of table")
    args = ap.parse_args()
    return asyncio.run(_main(args.json))


if __name__ == "__main__":
    sys.exit(main())
