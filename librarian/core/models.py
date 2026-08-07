"""Platform- and source-neutral domain models."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# Called with (downloaded_bytes, total_bytes); total may be 0 if unknown.
ProgressCallback = Callable[[int, int], Awaitable[None]]


@dataclass
class SearchResult:
    """A single searchable/downloadable item.

    ``source`` is the registry name of the Source that produced it and is used to
    dispatch the download back to that same source. ``ref`` is an opaque bag of
    source-specific handles (md5, guid, indexer id, direct url…): the core never
    inspects it, only the owning source does. This is what keeps adding a new
    source from touching any core code.
    """

    source: str
    title: str
    ext: str
    author: str = ""
    size_bytes: int = 0
    is_torrent: bool = False
    ref: dict[str, Any] = field(default_factory=dict)
