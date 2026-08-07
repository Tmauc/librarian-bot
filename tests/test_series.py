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


def _sparql_rows(labels):
    return {"results": {"bindings": [{"volLabel": {"value": v}} for v in labels]}}


def test_volumes_from_wikidata(monkeypatch):
    _install(
        monkeypatch,
        {"search": [{"id": "Q1"}]},
        _sparql_rows(["Le Feu dans le ciel", "Les Dragons", "Irianeth"]),
    )
    assert asyncio.run(series.volumes("Les Chevaliers")) == ["Le Feu dans le ciel", "Les Dragons", "Irianeth"]


def test_empty_name_returns_empty():
    assert asyncio.run(series.volumes("   ")) == []


def test_no_candidates(monkeypatch):
    _install(monkeypatch, {"search": []}, _sparql_rows([]))
    assert asyncio.run(series.volumes("inconnu")) == []


def test_skips_unlabelled_qid_volumes(monkeypatch):
    _install(monkeypatch, {"search": [{"id": "Q1"}]}, _sparql_rows(["Vrai Tome", "Q4567", "Autre Tome"]))
    assert asyncio.run(series.volumes("x")) == ["Vrai Tome", "Autre Tome"]
