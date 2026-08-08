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
    # Flow tests deliver fake files: build metadata locally (no Open Library call)
    # and never run the ebook-meta subprocess. Metadata itself is covered in
    # test_metadata.py, which drives build/enrich/apply directly.
    from librarian.core import metadata

    async def _prepare(result, hint=None):
        return metadata.build(result, hint)

    async def _apply(*args, **kwargs):
        return False

    monkeypatch.setattr(metadata, "prepare", _prepare)
    monkeypatch.setattr(metadata, "apply", _apply)
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


def test_single_search_groups_editions_and_offers_choice():
    """The list shows one row per book (editions grouped); opening a multi-edition book
    offers an edition chooser — so a popular title isn't a wall of near-duplicates."""
    async def scenario():
        registry._ALL[:] = [StubSource([
            SearchResult("stub", "Dune", "epub", size_bytes=1000, ref={"md5": "a"}),
            SearchResult("stub", "Dune", "epub", size_bytes=2000, ref={"md5": "b"}),
            SearchResult("stub", "Foundation", "epub", size_bytes=1000, ref={"md5": "c"}),
        ])]
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        # group 0 = Dune (2 editions) → pick edition "1" → Télécharger → epub
        await drive(s, flow.run_search(ctx, "dune"),
                    [("choice", "0"), ("choice", "1"), ("choice", "dl"), ("choice", "epub")])
        return ctx

    ctx = asyncio.run(scenario())
    assert len(ctx.docs) == 1
    assert any("2 livre(s)" in m for m in ctx.messages)   # 3 results → 2 books listed
    assert any("2 éditions" in m for m in ctx.messages)   # edition chooser was shown


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


def test_fallback_plan_cleans_query_and_reads_language():
    from librarian.clients.flow import _fallback_plan

    p = _fallback_plan("L'intégrale d'Hunger games en vf")
    assert p.query == "Hunger games" and p.language == "fr" and p.series is True
    assert _fallback_plan("toute la saga Harry Potter").query == "Harry Potter"
    assert _fallback_plan("la trilogie Millénium en anglais").language == "en"


def test_batch_hints_cover_stem_and_more_phrasings():
    from librarian.clients.flow import _looks_like_batch

    assert _looks_like_batch("l'intégral de hunger games")   # 'intégral' without final e
    assert _looks_like_batch("integral hunger games")        # no accent, no e
    assert _looks_like_batch("le coffret harry potter")
    assert _looks_like_batch("toute la collection X")
    assert _looks_like_batch("l'intégrale épée de la vérité en vf")
    assert not _looks_like_batch("hunger games tome 2")      # a single volume stays single-search
    assert not _looks_like_batch("dune")


def test_clean_series_query_scrubs_leftover_keyword():
    """The small LLM sometimes leaves « intégrale » in the query; we scrub it so Wikidata
    and the catalogue search the bare series name (the Épée de Vérité bug)."""
    from librarian.clients.flow import _clean_series_query

    assert _clean_series_query("intégrale épée de la vérité") == "épée de la vérité"
    assert _clean_series_query("L'intégrale d'Hunger games en vf") == "Hunger games"
    assert _clean_series_query("Hunger Games") == "Hunger Games"  # already clean → untouched


def test_batch_triggers_without_llm_via_fallback(monkeypatch):
    """« l'intégrale de X » must still enter series mode when the LLM is down — not fall
    back to a plain 25-result search (the bug the user hit)."""
    from librarian.core import planner, series

    async def no_vols(name, language="fr"):
        return []

    async def scenario():
        registry._ALL[:] = [StubSource(_epub_results())]  # 2 catalogue results
        monkeypatch.setattr(planner, "enabled", lambda: False)  # LLM unavailable
        monkeypatch.setattr(series, "volumes", no_vols)
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_search(ctx, "l'intégrale de dune"), [("choice", "0"), ("choice", "1")])
        return ctx

    ctx = asyncio.run(scenario())
    assert len(ctx.docs) == 2  # batch ran (multi-select), not a single search
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


def test_batch_falls_back_across_editions(monkeypatch):
    """A tome whose first edition's mirrors are dead falls back to the next edition
    instead of failing — the fix for the frequent 'sources indisponibles' in batch."""
    from librarian.core import planner, series
    from librarian.core.models import Plan, SearchResult

    async def fake_plan(request):
        return Plan(query="Ma série", series=True, desired_format="epub")

    async def fake_vols(name, language="fr"):
        return [(1, "Alpha")]

    class FlakySource(Source):
        name = "stub"

        async def search(self, query):
            if "Alpha" in query:  # two editions of the same volume
                return [
                    SearchResult("stub", "Ma série Alpha ed1", "epub", ref={"md5": "dead"}),
                    SearchResult("stub", "Ma série Alpha ed2", "epub", ref={"md5": "ok"}),
                ]
            return []

        async def download(self, result, on_progress=None, max_bytes=0):
            if result.ref.get("md5") == "dead":
                raise RuntimeError("All mirrors failed for md5=dead")
            path = tempfile.mktemp(suffix=".epub", prefix="librarian_")
            with open(path, "wb") as f:
                f.write(b"x" * 5000)
            return path

    async def scenario():
        registry._ALL[:] = [FlakySource()]
        monkeypatch.setattr(planner, "enabled", lambda: True)
        monkeypatch.setattr(planner, "plan", fake_plan)
        monkeypatch.setattr(series, "volumes", fake_vols)
        await prefs.set("test:1", "format", "epub")
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_search(ctx, "l'intégrale de ma série"), [("choice", "0")])
        return ctx

    ctx = asyncio.run(scenario())
    assert len(ctx.docs) == 1  # delivered via the 2nd edition despite the 1st failing
    assert "1/1" in ctx.messages[-1]


def test_best_matches_prefers_language_then_format():
    from librarian.clients.flow import _best_matches
    from librarian.core.models import SearchResult

    results = [
        SearchResult("s", "Hunger Games", "pdf", language="English"),
        SearchResult("s", "Hunger Games", "epub", language="English"),
        SearchResult("s", "Hunger Games", "pdf", language="Français"),
        SearchResult("s", "Hunger Games", "epub", language="Français"),
    ]
    ranked = _best_matches("Hunger Games", 1, results, language="fr", fmt="epub")
    # French EPUB first, then French PDF, then the English ones.
    assert (ranked[0].language, ranked[0].ext) == ("Français", "epub")
    assert (ranked[1].language, ranked[1].ext) == ("Français", "pdf")


def test_best_matches_uses_tome_number():
    """A French edition titled by tome number is matched even without the sub-title."""
    from librarian.clients.flow import _best_matches
    from librarian.core.models import SearchResult

    results = [
        SearchResult("s", "Hunger Games - L'Embrasement", "epub", language="English"),
        SearchResult("s", "Hunger Games, tome 2", "epub", language="Français"),  # no sub-title
    ]
    ranked = _best_matches("L'Embrasement", 2, results, language="fr", fmt="epub")
    assert ranked[0].title == "Hunger Games, tome 2"  # FR + tome-number match wins


def test_batch_epub_target_skips_non_epub_candidates():
    """desired_fmt=epub must never deliver a PDF (we can't convert *to* epub)."""
    from librarian.destinations.here import ThisChatDestination

    async def scenario():
        results = [
            SearchResult("stub", "Only PDF", "pdf", size_bytes=1000, ref={"id": 1}),
            SearchResult("stub", "Real EPUB", "epub", size_bytes=1000, ref={"id": 2}),
        ]
        registry._ALL[:] = [StubSource(results)]
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow._deliver(ctx, results, 0, "epub", ThisChatDestination()), [])
        return ctx

    ctx = asyncio.run(scenario())
    assert ctx.docs and ctx.docs[0][0].endswith(".epub")  # PDF skipped, EPUB delivered


def test_best_matches_edition_preference_breaks_ties():
    from librarian.clients.flow import _best_matches
    from librarian.core.models import SearchResult

    small = SearchResult("s", "Hunger Games, tome 1", "epub", language="Français", size_bytes=300_000, year="2011")
    big = SearchResult("s", "Hunger Games, tome 1", "epub", language="Français", size_bytes=1_300_000, year="2015")
    # quality → biggest file; light → smallest; recent → newest year
    assert _best_matches("Hunger Games", 1, [small, big], "fr", "epub", "quality")[0] is big
    assert _best_matches("Hunger Games", 1, [big, small], "fr", "epub", "light")[0] is small
    assert _best_matches("Hunger Games", 1, [small, big], "fr", "epub", "recent")[0] is big


def test_settings_changes_edition_preference():
    async def scenario():
        s = Session("test:1")
        ctx = FakeContext(s)
        await drive(s, flow.run_settings(ctx), [("choice", "edition"), ("choice", "light"), ("choice", "close")])
        return await prefs.get("test:1")

    assert asyncio.run(scenario()).get("edition_pref") == "light"


def test_multi_choice_select_all_returns_every_value():
    from librarian.clients.base import ALL, Choice

    async def scenario():
        s = Session("test:1")
        ctx = FakeContext(s)
        task = asyncio.create_task(
            ctx.ask_multi_choice("pick", [Choice("A", "0"), Choice("B", "1"), Choice("C", "2")])
        )
        s.task = task
        for _ in range(100):
            await asyncio.sleep(0.005)
            if s.is_waiting():
                s.resolve_choice(ALL)
                break
        return await task

    assert asyncio.run(scenario()) == ["0", "1", "2"]


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
