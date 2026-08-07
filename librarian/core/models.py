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
    """A search intent produced by the intelligence layer from a free-text request.

    We do NOT ask the LLM to enumerate volumes (a small local model hallucinates them
    for niche series). Instead it extracts a clean ``query`` (the series/book name) +
    ``language``/``desired_format`` hints and whether it's a multi-book request
    (``series``). The real volumes then come from the source catalogue.
    """

    query: str
    language: str = ""
    desired_format: str = ""
    series: bool = False
