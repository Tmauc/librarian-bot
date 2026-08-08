"""A small local record of what the bot uploaded to each cloud destination.

Why: re-organising a cloud folder after the user changes their sort scheme must move
**only the files the bot placed** (never the user's manually-added files) and must do so
without re-downloading. We remember, per upload, the book's identity (author/series/
filename), its current folder path, and the provider-specific handle needed to move it
(Drive file id + parent, or Dropbox path). Re-sort recomputes the path for the new scheme
and issues a cheap server-side move.

Stored as JSON ``{provider: [records]}`` next to ``user_prefs.json`` (gitignored). Records
are opaque to this module beyond the dedup key (author, series, filename).
"""

import asyncio
import contextlib
import json
import os
import tempfile

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_default_file = os.path.join(_repo_root, "user_manifest.json")
MANIFEST_FILE = os.environ.get("USER_MANIFEST_FILE") or _default_file
_lock = asyncio.Lock()


def _read() -> dict:
    if not os.path.exists(MANIFEST_FILE):
        return {}
    try:
        with open(MANIFEST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _dedup_key(rec: dict) -> tuple:
    return (rec.get("author", ""), rec.get("series", ""), rec.get("filename", ""))


async def add(provider: str, record: dict) -> None:
    """Record (or replace) an uploaded file for ``provider``. Dedups on the same book."""
    async with _lock:
        data = _read()
        recs = [r for r in data.get(provider, []) if _dedup_key(r) != _dedup_key(record)]
        recs.append(record)
        data[provider] = recs
        _atomic_write(data)


async def records(provider: str) -> list[dict]:
    async with _lock:
        return list(_read().get(provider, []))


async def save(provider: str, recs: list[dict]) -> None:
    """Replace the whole record list for ``provider`` (after a re-sort)."""
    async with _lock:
        data = _read()
        data[provider] = list(recs)
        _atomic_write(data)


def _atomic_write(data: dict) -> None:
    dest_dir = os.path.dirname(os.path.abspath(MANIFEST_FILE))
    os.makedirs(dest_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(text=True, suffix=".json", dir=dest_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, MANIFEST_FILE)
    except Exception:
        with contextlib.suppress(Exception):
            os.unlink(temp_path)
        raise
