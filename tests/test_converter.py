"""Regression lock for the EPUB conversion bugs (librarian.core.conversion)."""

import asyncio
import os

import pytest

from librarian.core import conversion


def _run(coro):
    return asyncio.run(coro)


def test_epub_to_pdf_produces_a_real_pdf(minimal_epub):
    out = _run(conversion.epub_to_pdf(minimal_epub))
    try:
        assert os.path.getsize(out) > 1024
        with open(out, "rb") as f:
            assert f.read(4) == b"%PDF", "output must be a genuine PDF, not renamed EPUB"
    finally:
        os.remove(out)


def test_epub_to_pdf_pages_are_readable(minimal_epub):
    import fitz

    out = _run(conversion.epub_to_pdf(minimal_epub))
    try:
        doc = fitz.open(out)
        assert doc.page_count >= 1
        assert doc.load_page(0).get_text().strip(), "first page should contain text"
        doc.close()
    finally:
        os.remove(out)


@pytest.mark.parametrize("fn", [conversion.epub_to_mobi, conversion.epub_to_azw3])
def test_mobi_azw3_require_calibre(minimal_epub, monkeypatch, fn):
    monkeypatch.setattr(conversion, "ebook_convert_available", lambda: False)
    with pytest.raises(RuntimeError, match="Calibre"):
        _run(fn(minimal_epub))


def test_mobi_azw3_no_leftover_temp_file_on_failure(minimal_epub, monkeypatch):
    import tempfile

    monkeypatch.setattr(conversion, "ebook_convert_available", lambda: False)

    def librarian_temps():
        return {f for f in os.listdir(tempfile.gettempdir()) if f.startswith("librarian_")}

    before = librarian_temps()
    with pytest.raises(RuntimeError):
        _run(conversion.epub_to_mobi(minimal_epub))
    assert not (librarian_temps() - before), "failed conversion must not leak a librarian_* temp file"
