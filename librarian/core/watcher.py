"""Watch a download folder for a torrent-produced book file (moved from watcher.py)."""

import asyncio
import logging
import os
import re

logger = logging.getLogger(__name__)

BOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3", ".azw", ".fb2"}


def _normalize(text: str) -> set[str]:
    """Return set of significant lowercase words (>3 chars) from a string."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return {w for w in text.split() if len(w) > 3}


def _matches(filename: str, title_words: set[str]) -> bool:
    """Check if a filename loosely matches the search title."""
    file_words = _normalize(os.path.splitext(filename)[0])
    if not title_words:
        return False
    overlap = title_words & file_words
    return len(overlap) >= max(1, len(title_words) // 2)


async def wait_for_file(title: str, download_path: str, timeout_minutes: int = 15) -> str:
    """Poll download_path until a new book file matching title appears.

    Returns the full path of the found file. Raises TimeoutError otherwise.
    """
    title_words = _normalize(title)
    timeout_seconds = timeout_minutes * 60

    try:
        existing = set(os.listdir(download_path))
    except FileNotFoundError:
        existing = set()

    logger.info(
        f"Watching {download_path!r} for {title!r} "
        f"(timeout={timeout_minutes}min, keywords={title_words})"
    )

    elapsed = 0
    while elapsed < timeout_seconds:
        await asyncio.sleep(5)
        elapsed += 5
        try:
            current = set(os.listdir(download_path))
        except FileNotFoundError:
            continue

        for fname in (current - existing):
            if os.path.splitext(fname)[1].lower() in BOOK_EXTENSIONS and _matches(fname, title_words):
                full_path = os.path.join(download_path, fname)
                logger.info(f"Found matching file: {full_path}")
                return full_path

    raise TimeoutError(
        f"File not found for '{title}' in {download_path} after {timeout_minutes} minutes."
    )
