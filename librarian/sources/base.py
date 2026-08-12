"""The Source contract.

A download provider implements this and gets registered once (see registry.py).
Nothing in ``librarian.core`` or ``librarian.clients`` needs to change to add one —
that is the whole point of this seam.
"""

from __future__ import annotations

import abc

from librarian.core.models import ProgressCallback, SearchResult


class Source(abc.ABC):
    #: unique registry name; also stored on each SearchResult it produces so the
    #: download can be dispatched back to this source.
    name: str = "source"

    @property
    def enabled(self) -> bool:
        """Whether this source is configured/usable. Disabled sources are skipped."""
        return True

    @abc.abstractmethod
    async def search(self, query: str) -> list[SearchResult]:
        """Return matching results (may be empty). Must not raise for a bad query —
        the search service isolates failures, but sources should fail soft."""

    @abc.abstractmethod
    async def download(
        self,
        result: SearchResult,
        on_progress: ProgressCallback | None = None,
        max_bytes: int = 0,
    ) -> str:
        """Fetch ``result`` (produced by this source) to a local file and return its
        path. ``max_bytes`` (0 = unlimited) lets the source abort oversized fetches
        early. Raise on failure."""

    async def details(self, result: SearchResult) -> dict:
        """Optionally fetch extra metadata for the detail card (e.g. a fuller
        description, a better cover). Returns a dict with any of: ``description``,
        ``cover``. Default: nothing extra. Should fail soft (return {})."""
        return {}

    async def available(self, result: SearchResult) -> bool:
        """Cheap, best-effort check that ``result`` can actually be downloaded right now
        (e.g. it exposes a live, non-gated mirror). Lets the client pre-filter dead-mirror
        books out of the offered list. Default: assume available. MUST fail soft — on any
        error return True rather than hide a possibly-good result."""
        return True
