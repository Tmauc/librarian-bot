"""Source registry — the single place to register download providers.

To add a source: create ``librarian/sources/<name>.py`` with a ``Source`` subclass,
then add it to ``_ALL`` below (or call ``register()`` at startup). No core or client
code changes.
"""

import logging

from librarian.sources.anna import AnnaArchiveSource
from librarian.sources.base import Source
from librarian.sources.prowlarr import ProwlarrSource

logger = logging.getLogger(__name__)

_ALL: list[Source] = [
    AnnaArchiveSource(),
    ProwlarrSource(),
]


def register(source: Source) -> None:
    """Add a source at runtime (e.g. from a plugin)."""
    _ALL.append(source)


def all_sources() -> list[Source]:
    return list(_ALL)


def enabled_sources() -> list[Source]:
    return [s for s in _ALL if s.enabled]


def get(name: str) -> Source | None:
    for s in _ALL:
        if s.name == name:
            return s
    return None
