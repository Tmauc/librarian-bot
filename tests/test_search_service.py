"""Search orchestration: fan-out, ordering, oversize filter, dedup."""

import asyncio

import pytest

from librarian.core import search_service
from librarian.core.models import SearchResult
from librarian.sources import registry
from librarian.sources.base import Source


class _Stub(Source):
    def __init__(self, name, items):
        self.name = name
        self._items = items

    async def search(self, query):
        return self._items

    async def download(self, result, on_progress=None, max_bytes=0):
        return "/tmp/x"


class _Boom(Source):
    name = "boom"

    async def search(self, query):
        raise RuntimeError("source down")

    async def download(self, result, on_progress=None, max_bytes=0):
        return "/tmp/x"


@pytest.fixture(autouse=True)
def restore_registry():
    saved = list(registry._ALL)
    yield
    registry._ALL[:] = saved


def test_merge_orders_epub_first_and_dedups_and_filters_oversize():
    registry._ALL[:] = [
        _Stub("a", [
            SearchResult("a", "Dune", "pdf", size_bytes=10),
            SearchResult("a", "Dune Messiah", "epub", size_bytes=20),
        ]),
        _Stub("b", [
            SearchResult("b", "Dune", "epub", size_bytes=15, is_torrent=True),   # dup title
            SearchResult("b", "Foundation", "epub", size_bytes=99_999_999_999),  # oversize
        ]),
    ]
    res = asyncio.run(search_service.search("x", max_file_size=50 * 1024 * 1024))
    titles = [(r.title, r.ext, r.is_torrent) for r in res]
    assert titles == [("Dune Messiah", "epub", False), ("Dune", "pdf", False)]


def test_a_failing_source_does_not_break_search():
    registry._ALL[:] = [
        _Boom(),
        _Stub("ok", [SearchResult("ok", "Neuromancer", "epub")]),
    ]
    res = asyncio.run(search_service.search("x"))
    assert [r.title for r in res] == ["Neuromancer"]


def test_distinct_series_volumes_are_kept():
    registry._ALL[:] = [
        _Stub("a", [
            SearchResult("a", "The Complete Works Volume 1", "epub"),
            SearchResult("a", "The Complete Works Volume 2", "epub"),
        ]),
    ]
    res = asyncio.run(search_service.search("x"))
    assert len(res) == 2
