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
    # Optional display metadata (filled when the source has it; used for the rich
    # result list and the detail card). Sources that don't provide them leave them blank.
    year: str = ""
    language: str = ""
    cover: str = ""        # cover image URL
    description: str = ""
    ref: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """A search plan produced by the intelligence layer from a free-text request.

    ``queries`` is one search per wanted book (several for a series/intégrale, one
    otherwise). ``language``/``desired_format`` are optional hints; ``title`` is a
    human label for the batch; ``series`` marks a multi-volume intent.
    """

    queries: list[str]
    language: str = ""
    desired_format: str = ""
    title: str = ""
    series: bool = False
