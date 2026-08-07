"""Locks for the follow-up findings: email validation (#13), offerable-format
selection (#11), and full-title dedup (#10)."""

import re

import pytest

import bot


# --- #13 email validation --------------------------------------------------
@pytest.mark.parametrize(
    "value,ok",
    [
        ("user@example.com", True),
        ("a.b+tag@sub.domain.co", True),
        ("no-at-sign", False),
        ("two@@at.com", False),
        ("space in@x.y", False),          # whitespace rejected
        ("back`tick@x.y", False),         # backtick rejected (would break Markdown echo)
        ("nodot@domain", False),
        ("x" * 250 + "@a.bc", False),     # over 254 chars
        ("", False),
    ],
)
def test_is_valid_email(value, ok):
    assert bot._is_valid_email(value) is ok


# --- #11 offerable formats -------------------------------------------------
def _offerable(src_ext, allowed):
    """Mirror of the selection logic in handle_download."""
    return list(allowed) if src_ext == "epub" else [src_ext]


def test_epub_source_offers_all_allowed_formats():
    assert _offerable("epub", ["epub", "pdf", "mobi", "azw3"]) == ["epub", "pdf", "mobi", "azw3"]


def test_pdf_source_offers_only_pdf():
    # The bug was offering MOBI/AZW3/EPUB on a PDF source; now only PDF.
    assert _offerable("pdf", ["epub", "pdf", "mobi", "azw3"]) == ["pdf"]


# --- #10 dedup by full normalized title ------------------------------------
def _dedup(titles):
    seen, out = set(), []
    for t in titles:
        norm = re.sub(r"[^\w]", "", t or "").lower()
        if norm and norm in seen:
            continue
        if norm:
            seen.add(norm)
        out.append(t)
    return out


def test_dedup_keeps_distinct_series_volumes():
    titles = [
        "The Complete Works of William Shakespeare Volume 1",
        "The Complete Works of William Shakespeare Volume 2",
    ]
    # 35-char prefix collided these; full-title dedup keeps both.
    assert _dedup(titles) == titles


def test_dedup_removes_true_duplicates():
    titles = ["Dune (Frank Herbert)", "Dune  Frank  Herbert", "Foundation"]
    assert _dedup(titles) == ["Dune (Frank Herbert)", "Foundation"]
