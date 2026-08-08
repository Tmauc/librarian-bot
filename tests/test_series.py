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


def test_empty_name_returns_empty():
    assert asyncio.run(series.volumes("   ")) == []


def test_no_candidates(monkeypatch):
    _install(monkeypatch, {"search": []}, _sparql_rows([]))
    assert asyncio.run(series.volumes("inconnu")) == []


def test_skips_unlabelled_qid_and_dupes(monkeypatch):
    _install(
        monkeypatch,
        {"search": [{"id": "Q1"}]},
        _sparql_rows([(1, "Vrai Tome"), (2, "Q4567"), (3, "Autre Tome"), (4, "vrai tome")]),
    )
    assert asyncio.run(series.volumes("x")) == [(1, "Vrai Tome"), (3, "Autre Tome")]
