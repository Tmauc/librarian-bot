"""End-to-end tests of the generic conversation flow, driven by a fake client.

Proves the platform-agnostic flow works without any messaging platform: the same
flow an adapter would drive is exercised here through a test-double ClientContext.
"""

import asyncio
import os
import tempfile

import pytest

from librarian.clients import flow
from librarian.clients.base import ClientContext, Session
from librarian.core import prefs
from librarian.core.models import SearchResult
from librarian.sources import registry
from librarian.sources.base import Source


class FakeContext(ClientContext):
    def __init__(self, session):
        super().__init__(session)
        self.messages = []
        self.docs = []
        self._mid = 0

    @property
    def max_file_size(self):
        return 50 * 1024 * 1024

    @staticmethod
    def _text(content):
        from librarian.clients.base import Card

        if isinstance(content, Card):
            parts = [content.title, content.description] + [f"{n}: {v}" for n, v in content.fields]
            return " ".join(p for p in parts if p)
        return content

    async def _send(self, content, choices=None):
        self._mid += 1
        self.messages.append(self._text(content))
        return self._mid

    async def _edit(self, handle, content, choices=None):
        self.messages.append(self._text(content))

    async def _disable(self, handle):
        pass

    async def _send_document(self, path, filename, caption):
        self.docs.append((filename, os.path.getsize(path)))


class StubSource(Source):
    name = "stub"

    def __init__(self, results):
        self._results = results

    async def search(self, query):
        return self._results

    async def download(self, result, on_progress=None, max_bytes=0):
        path = tempfile.mktemp(suffix=f".{result.ext}", prefix="librarian_")
        with open(path, "wb") as f:
            f.write(b"x" * 5000)
        if on_progress:
            await on_progress(5000, 5000)
        return path


async def drive(session, coro, responses):
    """Run a flow coroutine, feeding scripted responses whenever it waits."""
    task = asyncio.create_task(coro)
    session.task = task
    i = 0
    for _ in range(500):
        await asyncio.sleep(0.005)
        if task.done():
            break
        if session.is_waiting() and i < len(responses):
            kind, val = responses[i]
            i += 1
            (session.resolve_text if kind == "text" else session.resolve_choice)(val)
    if not task.done():
        task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(prefs, "PREFS_FILE", str(tmp_path / "prefs.json"))
    saved = list(registry._ALL)
    yield
    registry._ALL[:] = saved


def _epub_results():
    return [
        SearchResult("stub", "Dune (Herbert)", "epub", size_bytes=1000, ref={"id": 1}),
        SearchResult("stub", "Foundation", "epub", size_bytes=1000, ref={"id": 2}),
    ]


def test_onboarding_sets_format_and_skips_addresses():
    async def scenario():
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_start(ctx), [("choice", "epub"), ("choice", "__skip__"), ("choice", "__skip__")])
        return await prefs.get("test:1")

    p = asyncio.run(scenario())
    assert p.get("format") == "epub"
    assert "email" not in p and "kindle_email" not in p


def test_search_downloads_and_delivers_document():
    async def scenario():
        registry._ALL[:] = [StubSource(_epub_results())]
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        # pick result 0, then choose epub format (ALLOWED_FORMATS = epub,pdf)
        # pick result 0 → detail card → Télécharger → format epub
        await drive(s, flow.run_search(ctx, "dune"), [("choice", "0"), ("choice", "dl"), ("choice", "epub")])
        return ctx

    ctx = asyncio.run(scenario())
    assert ctx.docs == [("Dune Herbert.epub", 5000)]
    assert "Envoyé" in ctx.messages[-1]


def test_no_results_message():
    async def scenario():
        registry._ALL[:] = [StubSource([])]
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_search(ctx, "zzz"), [])
        return ctx

    ctx = asyncio.run(scenario())
    assert any("Aucun résultat" in m for m in ctx.messages)


def test_search_offers_and_uses_email_destination(monkeypatch):
    from librarian.core import delivery

    sent = {}

    async def fake_send(path, filename, addr, kindle=False):
        sent.update(addr=addr, kindle=kindle)

    async def scenario():
        registry._ALL[:] = [StubSource(_epub_results())]
        monkeypatch.setattr(delivery, "is_configured", lambda: True)
        monkeypatch.setattr(delivery, "send_file", fake_send)
        await prefs.set("test:1", "format", "epub")
        await prefs.set("test:1", "email", "me@example.com")
        s = Session("test:1")
        ctx = FakeContext(s)
        # pick result 0 → format epub → destination menu (here/email) → choose email
        await drive(
            s, flow.run_search(ctx, "dune"),
            [("choice", "0"), ("choice", "dl"), ("choice", "epub"), ("choice", "email")],
        )
        return ctx

    ctx = asyncio.run(scenario())
    assert sent == {"addr": "me@example.com", "kindle": False}
    assert not ctx.docs, "email destination must not upload to the chat"


def test_batch_fallback_multiselect(monkeypatch):
    """Series unknown to Wikidata → raw catalogue multi-select."""
    from librarian.core import planner, series
    from librarian.core.models import Plan

    async def fake_plan(request):
        return Plan(query="Ma série", series=True, desired_format="epub")

    async def no_vols(name, language="fr"):
        return []

    async def scenario():
        registry._ALL[:] = [StubSource(_epub_results())]  # 2 results
        monkeypatch.setattr(planner, "enabled", lambda: True)
        monkeypatch.setattr(planner, "plan", fake_plan)
        monkeypatch.setattr(series, "volumes", no_vols)
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_search(ctx, "l'intégrale de ma série"), [("choice", "0"), ("choice", "1")])
        return ctx

    ctx = asyncio.run(scenario())
    assert len(ctx.docs) == 2
    assert "Terminé" in ctx.messages[-1]


def test_batch_series_from_wikidata(monkeypatch):
    """Known series → Wikidata volumes matched to catalogue files, then multi-select."""
    from librarian.core import planner, series
    from librarian.core.models import Plan, SearchResult

    async def fake_plan(request):
        return Plan(query="Ma série", series=True, desired_format="epub")

    async def fake_vols(name, language="fr"):
        return [(1, "Alpha"), (2, "Beta")]

    class SeriesStub(Source):
        name = "stub"

        async def search(self, query):
            for word in ("Alpha", "Beta"):
                if word in query:
                    return [SearchResult("stub", f"Ma série {word}", "epub", ref={})]
            return []

        async def download(self, result, on_progress=None, max_bytes=0):
            path = tempfile.mktemp(suffix=".epub", prefix="librarian_")
            with open(path, "wb") as f:
                f.write(b"x" * 5000)
            return path

    async def scenario():
        registry._ALL[:] = [SeriesStub()]
        monkeypatch.setattr(planner, "enabled", lambda: True)
        monkeypatch.setattr(planner, "plan", fake_plan)
        monkeypatch.setattr(series, "volumes", fake_vols)
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_search(ctx, "l'intégrale de ma série"), [("choice", "0"), ("choice", "1")])
        return ctx

    ctx = asyncio.run(scenario())
    assert len(ctx.docs) == 2  # both matched volumes downloaded


def test_settings_changes_format():
    async def scenario():
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_settings(ctx), [("choice", "fmt"), ("choice", "pdf"), ("choice", "close")])
        return await prefs.get("test:1")

    p = asyncio.run(scenario())
    assert p.get("format") == "pdf"


def test_cancel_during_choice_cancels_flow_cleanly():
    async def scenario():
        registry._ALL[:] = [StubSource(_epub_results())]
        s = Session("test:1")
        ctx = FakeContext(s)
        task = asyncio.create_task(flow.run_search(ctx, "dune"))
        s.task = task
        for _ in range(200):
            await asyncio.sleep(0.005)
            if s.is_waiting():
                s.cancel()
                break
        await asyncio.gather(task, return_exceptions=True)
        return task

    task = asyncio.run(scenario())
    assert task.cancelled()
