"""Metadata brick: build/enrich merge logic + the OPF-direct writer (Calibre-free).

The Calibre path is a subprocess we don't drive in unit tests; here we force the
OPF fallback and prove the EPUB is correctly rewritten (fields + valid packaging).
"""

import asyncio
import zipfile

from librarian.core import calibre, metadata
from librarian.core.metadata import BookMeta
from librarian.core.models import SearchResult


def test_clean_title_dedups_repeated_tail():
    assert metadata._clean_title("Le feu dans le ciel : Le feu dans le ciel") == "Le feu dans le ciel"
    assert metadata._clean_title("A : B : B") == "A : B"


def test_lang_code_maps_display_names():
    assert metadata._lang_code("Français") == "fr"
    assert metadata._lang_code("English") == "en"
    assert metadata._lang_code("fr") == "fr"
    assert metadata._lang_code("") == ""


def test_build_hint_wins_over_result():
    r = SearchResult("anna", "Messy Title : Messy Title", "epub", author="Anne Robillard",
                     year="2003", language="Français", cover="http://x/c.jpg")
    hint = BookMeta(title="Le Feu dans le ciel", series="Les Chevaliers d'Émeraude", index=1, language="fr")
    m = metadata.build(r, hint)
    assert m.title == "Le Feu dans le ciel"           # canonical hint title
    assert m.series == "Les Chevaliers d'Émeraude"
    assert m.index == 1
    assert m.author == "Anne Robillard"                # from the result
    assert m.language == "fr" and m.year == "2003"


def test_build_without_hint_cleans_result_title():
    r = SearchResult("anna", "Dune : Dune", "epub", language="English")
    m = metadata.build(r)
    assert m.title == "Dune" and m.language == "en" and m.series == ""


def test_enrich_fills_gaps_from_open_library(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"docs": [{"isbn": ["9782890774", "x"], "cover_i": 42, "first_publish_year": 2002}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _Resp()

    monkeypatch.setattr(metadata.httpx, "AsyncClient", _Client)
    m = asyncio.run(metadata.enrich(BookMeta(title="Le Feu dans le ciel", author="Robillard")))
    assert m.isbn == "9782890774"
    assert m.year == "2002"
    assert m.cover_url.endswith("/42-L.jpg")


def test_apply_opf_rewrites_fields_and_keeps_valid_packaging(monkeypatch, minimal_epub):
    monkeypatch.setattr(calibre, "tool", lambda name: None)  # force the OPF fallback
    meta = BookMeta(title="Le Feu dans le ciel", author="Anne Robillard",
                    series="Les Chevaliers d'Émeraude", index=1, language="fr", year="2003")
    ok = asyncio.run(metadata.apply(minimal_epub, "epub", meta))
    assert ok

    with zipfile.ZipFile(minimal_epub) as z:
        names = z.namelist()
        # EPUB OCF: mimetype must be first AND stored uncompressed.
        assert names[0] == "mimetype"
        assert z.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        opf = z.read("OEBPS/content.opf").decode("utf-8")
    assert "<dc:title>Le Feu dans le ciel</dc:title>" in opf
    assert "Anne Robillard" in opf
    assert 'name="calibre:series" content="Les Chevaliers d&quot;Émeraude"' not in opf  # apostrophe, not quote
    assert 'calibre:series' in opf and "Les Chevaliers d'Émeraude" in opf
    assert 'calibre:series_index' in opf and 'content="1"' in opf
    assert "<dc:language>fr</dc:language>" in opf


def test_apply_opf_is_idempotent(monkeypatch, minimal_epub):
    monkeypatch.setattr(calibre, "tool", lambda name: None)
    meta = BookMeta(title="T", author="A", series="S", index=2, language="fr")
    assert asyncio.run(metadata.apply(minimal_epub, "epub", meta))
    assert asyncio.run(metadata.apply(minimal_epub, "epub", meta))  # second pass must not corrupt
    with zipfile.ZipFile(minimal_epub) as z:
        opf = z.read("OEBPS/content.opf").decode("utf-8")
    assert opf.count("<dc:title>") == 1                # not duplicated
    assert opf.count('name="calibre:series"') == 1
