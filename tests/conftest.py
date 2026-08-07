"""Shared test fixtures/helpers.

Tests import the bot modules directly. A couple of them (``bot``, ``anna_archive``)
read env vars at import time, so we set harmless defaults before anything imports
them.
"""

import os
import zipfile

import pytest

os.environ.setdefault("TELEGRAM_TOKEN", "123:test")
os.environ.setdefault("ALLOWED_USER_IDS", "1")


def build_minimal_epub(path: str) -> str:
    """Write a small but structurally valid EPUB to ``path`` and return it."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?>'
            '<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" "
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
            'unique-identifier="id">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<dc:title>Test Book</dc:title>"
            '<dc:identifier id="id">urn:uuid:test</dc:identifier>'
            "<dc:language>en</dc:language></metadata>"
            '<manifest><item id="c1" href="ch1.xhtml" '
            'media-type="application/xhtml+xml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>',
        )
        z.writestr(
            "OEBPS/ch1.xhtml",
            '<?xml version="1.0"?><!DOCTYPE html>'
            '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Ch1</title></head>'
            "<body><h1>Chapter One</h1>" + ("<p>word word word.</p>" * 80) + "</body></html>",
        )
    return path


@pytest.fixture
def minimal_epub(tmp_path):
    return build_minimal_epub(str(tmp_path / "book.epub"))
