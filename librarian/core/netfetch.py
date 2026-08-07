"""Reusable HTTP-streaming-to-tempfile helper, shared by sources."""

import contextlib
import logging
import os
import tempfile
import time

from librarian.core.models import ProgressCallback

logger = logging.getLogger(__name__)

# Content types we accept as a book file (others usually mean an error/HTML page).
VALID_CONTENT_TYPES = {
    "application/epub+zip",
    "application/pdf",
    "application/x-mobipocket-ebook",
    "application/octet-stream",
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


async def stream_to_tempfile(
    resp,
    ext: str,
    on_progress: ProgressCallback | None = None,
    max_bytes: int = 0,
) -> str | None:
    """Stream an already-open httpx response to a temp file with progress updates.

    Returns the temp file path, or None if the payload was too small to be a real
    file (<1 KB). Raises RuntimeError if it exceeds ``max_bytes``.

    Cleanup uses ``except BaseException`` so a cancellation mid-stream also removes
    the partial file (CancelledError is a BaseException, not an Exception).
    """
    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    last_report = 0.0
    last_pct = -1
    suffix = f".{ext}" if ext else ".epub"
    path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="librarian_") as f:
            path = f.name
            async for chunk in resp.aiter_bytes(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if max_bytes and downloaded > max_bytes:
                    raise RuntimeError(f"File too large (>{max_bytes // 1024 // 1024} MB)")
                if on_progress:
                    now = time.monotonic()
                    pct = int(downloaded / total * 100) if total else 0
                    if now - last_report >= 2.0 and pct != last_pct:
                        last_report = now
                        last_pct = pct
                        with contextlib.suppress(Exception):
                            await on_progress(downloaded, total)
        if downloaded < 1024:
            os.remove(path)
            return None
        return path
    except BaseException:
        if path:
            with contextlib.suppress(Exception):
                os.remove(path)
        raise
