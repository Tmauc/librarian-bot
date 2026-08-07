"""Regression locks for small pure helpers across the new package."""

import pytest

import main
from librarian.clients import flow
from librarian.core import watcher
from librarian.sources import anna


# --- main._is_newer_version ------------------------------------------------
@pytest.mark.parametrize(
    "remote,local,expected",
    [
        ("1.2.3", "1.2.2", True),
        ("v1.2.3", "1.2.2", True),
        ("1.2.10", "1.2.2", True),   # numeric compare, not lexical
        ("1.2.2", "1.2.2", False),
        ("1.2.1", "1.2.2", False),
        ("2.0.0-beta", "1.2.2", False),
    ],
)
def test_is_newer_version(remote, local, expected):
    assert main._is_newer_version(remote, local) is expected


# --- flow._fmt_size --------------------------------------------------------
def test_fmt_size():
    assert flow._fmt_size(0) == "?"
    assert flow._fmt_size(512).endswith("Ko")
    assert flow._fmt_size(5 * 1024 * 1024).endswith("Mo")


# --- anna parsing ----------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("2.3 MB", int(2.3 * 1024 * 1024)),
        ("450 KB", 450 * 1024),
        ("1,5 Mo", int(1.5 * 1024 * 1024)),
        ("no size here", 0),
    ],
)
def test_parse_size_from_text(text, expected):
    assert anna._parse_size_from_text(text) == expected


def test_validate_md5():
    assert anna._validate_md5("a" * 32)
    assert not anna._validate_md5("a" * 31)
    assert not anna._validate_md5("g" * 32)


def test_sanitize_ext():
    assert anna._sanitize_ext("EPUB") == "epub"
    assert anna._sanitize_ext("") == "epub"
    assert anna._sanitize_ext("../../etc") == "etc"


def test_extract_download_link_prefers_book_files():
    html = """
      <a href="/page.html">not a book</a>
      <a href="https://mirror.example/file.epub">download</a>
    """
    assert anna._extract_download_link(html, "https://mirror.example/") == "https://mirror.example/file.epub"


def test_extract_download_link_resolves_relative():
    html = '<a href="get.php?md5=abc">get</a>'
    out = anna._extract_download_link(html, "https://libgen.li/ads.php?x=1")
    assert out == "https://libgen.li/get.php?md5=abc"


# --- watcher matching ------------------------------------------------------
def test_watcher_normalize_drops_short_words():
    assert watcher._normalize("The Art of Warfare") == {"warfare"}


def test_watcher_matches_on_half_overlap():
    words = watcher._normalize("Dune Messiah Frank Herbert")
    assert watcher._matches("Dune.Messiah.Herbert.epub", words)
    assert not watcher._matches("Completely.Unrelated.Title.epub", words)
