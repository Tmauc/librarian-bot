"""Generic download dispatch: route a result back to its owning source.

The auto-retry/size-guard/convert/scan orchestration lives in the client flow
(it needs the result list and the UI); this seam just hides the registry.
"""

import logging

from librarian.core.models import ProgressCallback, SearchResult
from librarian.sources import registry

logger = logging.getLogger(__name__)


async def fetch(
    result: SearchResult,
    on_progress: ProgressCallback | None = None,
    max_bytes: int = 0,
) -> str:
    """Download ``result`` via the source that produced it. Returns a local path."""
    source = registry.get(result.source)
    if source is None:
        raise ValueError(f"Unknown source: {result.source!r}")
    return await source.download(result, on_progress, max_bytes)


async def details(result: SearchResult) -> dict:
    """Fetch optional extra metadata (description/cover) via the owning source."""
    source = registry.get(result.source)
    if source is None:
        return {}
    return await source.details(result)
