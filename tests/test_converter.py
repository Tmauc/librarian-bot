"""Regression lock for the EPUB conversion bugs.

Before the fix, ``epub_to_pdf`` called ``Document.save()`` on an EPUB, which raises
``AssertionError`` inside PyMuPDF — so PDF was never actually produced, and the
MOBI/AZW3 fallback wrote PDF bytes under a .mobi/.azw3 name.
"""

import asyncio
import os

import pytest

import converter


def _run(coro):
    return asyncio.run(coro)


def test_epub_to_pdf_produces_a_real_pdf(minimal_epub):
    out = _run(converter.epub_to_pdf(minimal_epub))
    try:
        assert os.path.getsize(out) > 1024
        with open(out, "rb") as f:
            assert f.read(4) == b"%PDF", "output must be a genuine PDF, not renamed EPUB"
    finally:
        os.remove(out)


def test_epub_to_pdf_pages_are_readable(minimal_epub):
    import fitz

    out = _run(converter.epub_to_pdf(minimal_epub))
    try:
        doc = fitz.open(out)
        assert doc.page_count >= 1
        assert doc.load_page(0).get_text().strip(), "first page should contain text"
        doc.close()
    finally:
        os.remove(out)


@pytest.mark.parametrize("fn", [converter.epub_to_mobi, converter.epub_to_azw3])
def test_mobi_azw3_require_calibre(minimal_epub, monkeypatch, fn):
    # Force the "no Calibre" path and assert we raise a clear error instead of
    # silently emitting an invalid file.
    monkeypatch.setattr(converter, "ebook_convert_available", lambda: False)
    with pytest.raises(RuntimeError, match="Calibre"):
        _run(fn(minimal_epub))


def test_mobi_azw3_no_leftover_temp_file_on_failure(minimal_epub, monkeypatch):
    import tempfile

    monkeypatch.setattr(converter, "ebook_convert_available", lambda: False)

    def librarian_temps():
        return {f for f in os.listdir(tempfile.gettempdir()) if f.startswith("librarian_")}

    before = librarian_temps()
    with pytest.raises(RuntimeError):
        _run(converter.epub_to_mobi(minimal_epub))
    assert not (librarian_temps() - before), "failed conversion must not leak a librarian_* temp file"
