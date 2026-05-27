"""Mint a service token against a registered platform.

Diagnostic CLI for Phase 2 of the multi-tenant migration. Picks the
first registered platform (or the one whose platform_id you pass) and
attempts the JWT-bearer client_credentials exchange against its token
endpoint.

Usage::

    docker compose exec api-gateway python -m scripts.test_service_token
    docker compose exec api-gateway python -m scripts.test_service_token --platform <pid>
    docker compose exec api-gateway python -m scripts.test_service_token --scope <scope>

What this proves end-to-end:

  * Our JWKS is reachable from Canvas (the kid in our assertion matches
    a key the platform can verify).
  * Canvas accepts our Developer Key as a client_credentials principal.
  * The scopes we are asking for are actually granted in the Developer
    Key admin page on the platform side.

A successful run prints the granted scope set and the bearer's TTL.
A 4xx from Canvas surfaces the ``error_description`` the platform sent
so you can map it back to a missing scope or a misconfigured key.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from redis.asyncio import Redis

from src.canvas.oauth import ServiceTokenError, get_service_token
from src.config import settings
from src.lti.platform_store import get_platform, list_platforms


async def _main(platform_id: str | None, scopes: list[str], as_json: bool) -> int:
    redis = Redis.from_url(settings.redis_url, decode_responses=False)
    try:
        if platform_id:
            platform = await get_platform(redis, platform_id)
            if platform is None:
                print(f"No platform with id {platform_id!r}", file=sys.stderr)
                return 2
        else:
            records = await list_platforms(redis)
            if not records:
                print(
                    "No platforms registered. Trigger an LTI launch from Canvas first.",
                    file=sys.stderr,
                )
                return 2
            platform = records[0]
            print(
                f"Using first registered platform: "
                f"{platform.platform_id} ({platform.canvas_domain})\n",
                file=sys.stderr,
            )

        try:
            cached = await get_service_token(
                redis, platform, scopes, force_refresh=True
            )
        except ServiceTokenError as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1

        ttl_remaining = int(cached.expires_at - __import__("time").time())
        result: dict[str, Any] = {
            "platform_id": platform.platform_id,
            "canvas_domain": platform.canvas_domain,
            "token_url": platform.auth_token_url,
            "requested_scopes": scopes,
            "granted_scope": cached.granted_scope,
            "ttl_seconds_remaining": ttl_remaining,
            "access_token_preview": cached.access_token[:12] + "..." + cached.access_token[-4:],
        }
        if as_json:
            json.dump(result, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"OK")
            for k, v in result.items():
                print(f"  {k}: {v}")
        return 0
    finally:
        await redis.aclose()


def main() -> int:
    ap = argparse.ArgumentParser(description="Mint and inspect a service token.")
    ap.add_argument(
        "--platform",
        dest="platform_id",
        default=None,
        help="Platform id (from `list_platforms`); defaults to first registered",
    )
    ap.add_argument(
        "--scope",
        dest="scopes",
        action="append",
        default=None,
        help="A scope to request (repeatable). Default: a small read-only set.",
    )
    ap.add_argument("--json", action="store_true", help="Emit JSON")
    args = ap.parse_args()

    # A default scope set that exercises both Canvas-specific and IMS
    # standard scope formats so a successful response confirms both
    # styles are parsed correctly on the platform side.
    scopes = args.scopes or [
        "url:GET|/api/v1/courses/:course_id/files",
        "url:GET|/api/v1/files/:id",
    ]
    return asyncio.run(_main(args.platform_id, scopes, args.json))


if __name__ == "__main__":
    sys.exit(main())
