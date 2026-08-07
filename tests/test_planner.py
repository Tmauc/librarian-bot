"""Locks for the intelligence layer (LLM planner). The Ollama call is mocked."""

import asyncio
import json

from librarian import config
from librarian.core import planner


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHTTP:
    def __init__(self, response_text):
        self._text = response_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        return _FakeResp({"response": self._text})


def _mock_llm(monkeypatch, model, response_text):
    monkeypatch.setattr(config, "LLM_MODEL", model)
    monkeypatch.setattr(planner.httpx, "AsyncClient", lambda *a, **k: _FakeHTTP(response_text))


def test_disabled_without_model(monkeypatch):
    monkeypatch.setattr(config, "LLM_MODEL", "")
    assert planner.enabled() is False
    assert asyncio.run(planner.plan("l'intégrale de X")) is None


def test_series_plan(monkeypatch):
    payload = json.dumps(
        {"query": "Les Chevaliers d'Émeraude", "language": "fr", "format": "epub", "series": True}
    )
    _mock_llm(monkeypatch, "test-model", payload)
    p = asyncio.run(planner.plan("l'intégrale des chevaliers d'émeraude en VF"))
    assert p is not None
    assert p.query == "Les Chevaliers d'Émeraude"
    assert p.language == "fr"
    assert p.desired_format == "epub"
    assert p.series is True


def test_single_plan(monkeypatch):
    _mock_llm(monkeypatch, "test-model", json.dumps({"query": "Dune Frank Herbert", "series": False}))
    p = asyncio.run(planner.plan("dune de frank herbert"))
    assert p.query == "Dune Frank Herbert"
    assert p.series is False


def test_bad_output_returns_none(monkeypatch):
    _mock_llm(monkeypatch, "test-model", "not json at all")
    assert asyncio.run(planner.plan("whatever")) is None
    _mock_llm(monkeypatch, "test-model", json.dumps({"query": ""}))
    assert asyncio.run(planner.plan("whatever")) is None


def test_invalid_format_dropped(monkeypatch):
    _mock_llm(monkeypatch, "test-model", json.dumps({"query": "x", "format": "cbr"}))
    p = asyncio.run(planner.plan("x"))
    assert p.desired_format == ""  # unknown format ignored
