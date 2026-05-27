"""CORS configuration.

The Canvas Theme-JS overlay runs from a Canvas hostname (e.g.
``canvas.example.edu``) and needs to call Reflow at a different origin
(e.g. ``reflow.example.edu`` or an ngrok dev tunnel). Some endpoints
need credentials (``/canvas/panorama/approve`` carries the LTI session
cookie); others don't (``/canvas/panorama/scored_files`` is anonymous).

Per the CORS spec, ``allow_origins=["*"]`` and ``allow_credentials=True``
are mutually exclusive. To support both we use ``allow_origin_regex``
that matches whichever hostnames the operator configures. For
credential-less fetches the regex still produces an echo, so they keep
working.

Portability
-----------
There is no campus-specific allow-list baked in. Operators choose which
hostnames are trusted via one of two settings:

  * ``canvas_allowed_origin_regex`` (env: ``CANVAS_ALLOWED_ORIGIN_REGEX``) -
    a regex of fully-qualified origins.
  * ``canvas_allowed_origins`` (env: ``CANVAS_ALLOWED_ORIGINS``) - a
    comma-separated list of literal origins, e.g.
    ``https://canvas.example.edu,https://example.instructure.com``.

The regex takes precedence when both are set. The default
(``DEFAULT_CANVAS_REGEX``) covers only the universal Canvas
``*.instructure.com`` domain and ngrok dev tunnels - every campus host
is opt-in via configuration. This makes the codebase deployable at any
institution without touching the source.
"""

from __future__ import annotations

import re

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import settings


# Out-of-the-box, trust only the universal Canvas cloud hostname and
# ngrok dev tunnels. Any campus-specific hostname must be added by the
# operator via CANVAS_ALLOWED_ORIGIN_REGEX or CANVAS_ALLOWED_ORIGINS.
DEFAULT_CANVAS_REGEX = (
    r"^https://("
    r"([a-z0-9-]+\.)+instructure\.com"
    r"|([a-z0-9-]+\.)+ngrok-free\.dev"
    r"|([a-z0-9-]+\.)+ngrok\.io"
    r")$"
)


def _build_regex_from_origins(origins):
    """Turn a list of literal origins into a single anchored regex.

    Each origin is regex-escaped so URL characters (dots especially) are
    matched literally rather than as "any char". The result is the union
    of all entries, anchored so it doesn't accept substrings.
    """
    escaped = [re.escape(o.strip()) for o in origins if o.strip()]
    if not escaped:
        return ""
    return r"^(?:" + "|".join(escaped) + r")$"


def _resolve_origin_regex():
    """Pick the right regex based on settings.

    Precedence (first non-empty wins):
      1. canvas_allowed_origin_regex - explicit operator override.
      2. canvas_allowed_origins - comma-separated literal origins
         compiled into a regex.
      3. DEFAULT_CANVAS_REGEX - instructure.com + ngrok dev tunnels.
    """
    explicit_regex = (getattr(settings, "canvas_allowed_origin_regex", "") or "").strip()
    if explicit_regex:
        return explicit_regex

    raw_list = (getattr(settings, "canvas_allowed_origins", "") or "").strip()
    if raw_list:
        origins = [o.strip() for o in raw_list.split(",") if o.strip()]
        compiled = _build_regex_from_origins(origins)
        if compiled:
            return compiled

    return DEFAULT_CANVAS_REGEX


def add_cors_middleware(app: FastAPI) -> None:
    """Wire CORS so the Canvas overlay can call Reflow with or without
    credentials. Origins matching the configured regex are echoed with
    credentials allowed; nothing else gets cross-origin access at all."""

    origin_regex = _resolve_origin_regex()

    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=origin_regex,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "X-API-Key",
            "X-Request-ID",
            # Required for the panorama overlay's state-changing POSTs
            # (approve / reject / request-edits / edit / pii-decision).
            # Without it the browser's CORS preflight strips the header
            # and the cross-origin POST fails with "Failed to fetch".
            "X-CSRF-Token",
            "Accept",
            "Origin",
            "Cache-Control",
            "ngrok-skip-browser-warning",
        ],
    )
