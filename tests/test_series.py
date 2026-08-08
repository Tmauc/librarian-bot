"""Locks for the Wikidata series lookup. HTTP is mocked."""

import asyncio

from librarian.core import series


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, api, sparql):
        self._api = api
        self._sparql = sparql

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return _Resp(self._api if "api.php" in url else self._sparql)


def _install(monkeypatch, api, sparql):
    monkeypatch.setattr(series.httpx, "AsyncClient", lambda *a, **k: _FakeClient(api, sparql))


def _sparql_rows(rows):
    # rows: list of (ord|None, label)
    out = []
    for ordv, label in rows:
        b = {"volLabel": {"value": label}}
        if ordv is not None:
            b["ord"] = {"value": str(ordv)}
        out.append(b)
    return {"results": {"bindings": out}}


def test_volumes_from_wikidata_with_ordinals(monkeypatch):
    _install(
        monkeypatch,
        {"search": [{"id": "Q1"}]},
        _sparql_rows([(1, "Le Feu dans le ciel"), (3, "Piège"), (12, "Irianeth")]),
    )
    assert asyncio.run(series.volumes("Les Chevaliers")) == [(1, "Le Feu dans le ciel"), (3, "Piège"), (12, "Irianeth")]


def test_missing_ordinal_is_none(monkeypatch):
    _install(monkeypatch, {"search": [{"id": "Q1"}]}, _sparql_rows([(None, "A"), (None, "B"), (None, "C")]))
    assert asyncio.run(series.volumes("x")) == [(None, "A"), (None, "B"), (None, "C")]


def test_resolve_drops_omnibus_and_returns_author(monkeypatch):
    api = {"search": [{"id": "Q1"}]}
    sparql = {"results": {"bindings": [
        {"volLabel": {"value": "L'Apprenti assassin"}, "ord": {"value": "1"}, "authorLabel": {"value": "Robin Hobb"}},
        {"volLabel": {"value": "L'Assassin du roi / La Nef du crépuscule"}, "ord": {"value": "2"},  # omnibus → dropped
         "authorLabel": {"value": "Robin Hobb"}},
        {"volLabel": {"value": "La Nef du crépuscule"}, "ord": {"value": "3"}, "authorLabel": {"value": "Robin Hobb"}},
    ]}}
    _install(monkeypatch, api, sparql)
    author, vols = asyncio.run(series.resolve("L'Assassin Royal"))
    assert author == "Robin Hobb"
    assert vols == [(1, "L'Apprenti assassin"), (3, "La Nef du crépuscule")]  # « A / B » omnibus removed


def test_author_filter_requires_every_name_word():
    from librarian.core.series import _author_filter

    f = _author_filter("Frank Herbert")
    assert "wdt:P50" in f
    assert 'CONTAINS(LCASE(STR(?authL)), "frank")' in f and '"herbert")' in f  # both words required
    assert _author_filter("") == ""  # no author → no filter


def test_empty_name_returns_empty():
    assert asyncio.run(series.volumes("   ")) == []


def test_no_candidates(monkeypatch):
    _install(monkeypatch, {"search": []}, _sparql_rows([]))
    assert asyncio.run(series.volumes("inconnu")) == []


def test_query_restricts_volumes_to_written_works(monkeypatch):
    """The SPARQL must require books (P31/P279* literary work) so film/game adaptations
    in the same Wikidata series are never offered as tomes (e.g. Hunger Games films)."""
    queries = []

    class _Cap:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if "api.php" in url:
                return _Resp({"search": [{"id": "Q1"}]})
            queries.append((params or {}).get("query", ""))
            return _Resp(_sparql_rows([]))

    monkeypatch.setattr(series.httpx, "AsyncClient", lambda *a, **k: _Cap())
    asyncio.run(series.volumes("Hunger Games"))
    members_q = next(q for q in queries if "?vol wdt:P179" in q)  # the volume-listing query
    assert "wdt:P31/wdt:P279*" in members_q and "Q7725634" in members_q


def test_skips_unlabelled_qid_and_dupes(monkeypatch):
    _install(
        monkeypatch,
        {"search": [{"id": "Q1"}]},
        _sparql_rows([(1, "Vrai Tome"), (2, "Q4567"), (3, "Autre Tome"), (4, "vrai tome")]),
    )
    assert asyncio.run(series.volumes("x")) == [(1, "Vrai Tome"), (3, "Autre Tome")]
