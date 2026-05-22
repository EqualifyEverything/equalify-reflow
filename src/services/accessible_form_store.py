"""Short-lived store for prepared accessible forms.

Holds ``(pdf_bytes, AccessibleForm)`` under an opaque id so the browser can load a
server-rendered form and submit it back without re-uploading the PDF. This is the
piece that makes the in-browser flow work: the form is served same-origin (no API
key, no CORS) and the original PDF stays on the server for write-back.

In-memory and single-process — fine for the dev container's single uvicorn worker.
For multi-worker or production, back this with Redis or S3 instead (same get/put
interface). Entries expire after ``_TTL_SECONDS``.
"""

from __future__ import annotations

import threading
import time
import uuid

from .accessible_form import AccessibleForm

_TTL_SECONDS = 3600
_lock = threading.Lock()
_store: dict[str, tuple[float, bytes, AccessibleForm]] = {}


def _gc(now: float) -> None:
    stale = [k for k, (ts, _, _) in _store.items() if now - ts > _TTL_SECONDS]
    for k in stale:
        _store.pop(k, None)


def put(pdf_bytes: bytes, form: AccessibleForm) -> str:
    """Store a (pdf, form) pair and return its id."""
    now = time.time()
    fid = uuid.uuid4().hex
    with _lock:
        _gc(now)
        _store[fid] = (now, pdf_bytes, form)
    return fid


def get(fid: str) -> tuple[bytes, AccessibleForm] | None:
    """Return ``(pdf_bytes, form)`` for an id, or None if missing/expired."""
    with _lock:
        entry = _store.get(fid)
    if not entry:
        return None
    return entry[1], entry[2]
