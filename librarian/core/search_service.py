"""Generic search orchestration: fan out to all enabled sources, merge, dedup.

Knows nothing about any specific source or client platform.
"""

import asyncio
import logging
import re

from librarian import config
from librarian.core.models import SearchResult
from librarian.sources import registry

logger = logging.getLogger(__name__)

# A batch (« l'intégrale de X ») fans out one search per volume — 17 tomes × 2 queries
# = 34 at once, which makes Anna return 429 Too Many Requests. Cap how many searches hit
# the sources concurrently so large series don't get rate-limited.
_MAX_CONCURRENT_SEARCHES = 3
_search_sem = asyncio.Semaphore(_MAX_CONCURRENT_SEARCHES)


async def _safe_search(source, query: str) -> list[SearchResult]:
    try:
        return await source.search(query)
    except Exception as e:
        logger.warning(f"{source.name} search error: {e}")
        return []


def _sort_key(r: SearchResult):
    return (
        0 if r.ext in ("epub", "mobi", "azw3") else 1,  # e-reader formats first
        0 if not r.is_torrent else 1,                   # direct before torrents
    )


def _normalize_title(title: str) -> str:
    return re.sub(r"[^\w]", "", title or "").lower()


async def search(query: str, max_file_size: int = 0) -> list[SearchResult]:
    """Search all enabled sources concurrently and return a merged, deduped list.

    ``max_file_size`` (0 = unlimited) drops results already known to be too big;
    the real size guard still happens post-download since many results report 0.
    """
    async with _search_sem:  # throttle concurrent source hits (avoid Anna 429 on big series)
        lists = await asyncio.gather(*[_safe_search(s, query) for s in registry.enabled_sources()])
    all_results = [r for lst in lists for r in lst]

    direct = [r for r in all_results if not r.is_torrent]
    torrents = [r for r in all_results if r.is_torrent]
    ordered = sorted(direct, key=_sort_key) + torrents

    if max_file_size:
        ordered = [r for r in ordered if not (r.size_bytes and r.size_bytes > max_file_size)]

    # Deduplicate by FILE identity (md5 when the source has it, else normalized title):
    # this keeps genuinely distinct editions of the same title — different translation,
    # publisher, year, size — so the user can compare and choose. Cap at MAX_RESULTS.
    seen: set[str] = set()
    out: list[SearchResult] = []
    for r in ordered:
        key = (r.ref or {}).get("md5") or _normalize_title(r.title)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(r)
        if len(out) >= config.MAX_RESULTS:
            break

    logger.info(f"Search '{query}': {len(out)} merged result(s) from {len(all_results)} raw")
    return out
