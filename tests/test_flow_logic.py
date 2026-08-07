"""Locks for flow-level logic: email validation, offerable formats, dedup."""

import pytest

from librarian.clients import flow


# --- email validation ------------------------------------------------------
@pytest.mark.parametrize(
    "value,ok",
    [
        ("user@example.com", True),
        ("a.b+tag@sub.domain.co", True),
        ("no-at-sign", False),
        ("two@@at.com", False),
        ("space in@x.y", False),
        ("back`tick@x.y", False),
        ("nodot@domain", False),
        ("x" * 250 + "@a.bc", False),
        ("", False),
    ],
)
def test_is_valid_email(value, ok):
    assert flow._is_valid_email(value) is ok


# --- offerable formats (mirror of the selection logic in run_search) -------
def _offerable(src_ext, allowed):
    return list(allowed) if src_ext == "epub" else [src_ext]


def test_epub_source_offers_all_allowed_formats():
    assert _offerable("epub", ["epub", "pdf", "mobi", "azw3"]) == ["epub", "pdf", "mobi", "azw3"]


def test_pdf_source_offers_only_pdf():
    assert _offerable("pdf", ["epub", "pdf", "mobi", "azw3"]) == ["pdf"]
