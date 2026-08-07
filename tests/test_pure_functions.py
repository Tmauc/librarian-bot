"""Regression locks for small pure helpers across the codebase."""

import pytest

import anna_archive
import watcher
import bot


# --- bot._is_newer_version -------------------------------------------------
@pytest.mark.parametrize(
    "remote,local,expected",
    [
        ("1.2.3", "1.2.2", True),
        ("v1.2.3", "1.2.2", True),
        ("1.2.10", "1.2.2", True),   # numeric compare, not lexical
        ("1.2.2", "1.2.2", False),
        ("1.2.1", "1.2.2", False),
        ("2.0.0-beta", "1.2.2", False),  # non-numeric tag → treated as (0,), never newer
    ],
)
def test_is_newer_version(remote, local, expected):
    assert bot._is_newer_version(remote, local) is expected


# --- bot._fmt_size ---------------------------------------------------------
def test_fmt_size():
    assert bot._fmt_size(0) == "?"
    assert bot._fmt_size(512).endswith("Ko")
    assert bot._fmt_size(5 * 1024 * 1024).endswith("Mo")


# --- anna_archive parsing --------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("2.3 MB", int(2.3 * 1024 * 1024)),
        ("450 KB", 450 * 1024),
        ("1,5 Mo", int(1.5 * 1024 * 1024)),  # French decimal comma + unit
        ("no size here", 0),
    ],
)
def test_parse_size_from_text(text, expected):
    assert anna_archive._parse_size_from_text(text) == expected


def test_validate_md5():
    assert anna_archive._validate_md5("a" * 32)
    assert not anna_archive._validate_md5("a" * 31)
    assert not anna_archive._validate_md5("g" * 32)  # non-hex


def test_sanitize_ext():
    assert anna_archive._sanitize_ext("EPUB") == "epub"
    assert anna_archive._sanitize_ext("") == "epub"
    assert anna_archive._sanitize_ext("../../etc") == "etc"


def test_extract_download_link_prefers_book_files():
    html = """
      <a href="/page.html">not a book</a>
      <a href="https://mirror.example/file.epub">download</a>
    """
    assert (
        anna_archive._extract_download_link(html, "https://mirror.example/")
        == "https://mirror.example/file.epub"
    )


def test_extract_download_link_resolves_relative():
    html = '<a href="get.php?md5=abc">get</a>'
    out = anna_archive._extract_download_link(html, "https://libgen.li/ads.php?x=1")
    assert out == "https://libgen.li/get.php?md5=abc"


# --- watcher matching ------------------------------------------------------
def test_watcher_normalize_drops_short_words():
    # Only words strictly longer than 3 chars survive ("the", "art", "of", "war"
    # are all <= 3 and dropped); "warfare" stays.
    assert watcher._normalize("The Art of Warfare") == {"warfare"}


def test_watcher_matches_on_half_overlap():
    words = watcher._normalize("Dune Messiah Frank Herbert")
    assert watcher._matches("Dune.Messiah.Herbert.epub", words)
    assert not watcher._matches("Completely.Unrelated.Title.epub", words)
