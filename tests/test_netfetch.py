"""Locks for the streaming helper — early magic-byte rejection."""

import asyncio
import os

import pytest

from librarian.core import netfetch


class _Resp:
    """Minimal stand-in for an open httpx streaming response."""

    def __init__(self, chunks, headers=None):
        self._chunks = chunks
        self.headers = headers or {}

    async def aiter_bytes(self, _n):
        for c in self._chunks:
            yield c


def test_rejects_non_epub_after_four_bytes():
    # An OLE2/Office document served under an .epub name (a real Anna corruption): must be
    # rejected on its leading bytes, NOT after streaming the whole multi-MB payload.
    resp = _Resp([b"\xd0\xcf\x11\xe0" + b"x" * 500_000])
    with pytest.raises(RuntimeError, match="Not a real"):
        asyncio.run(netfetch.stream_to_tempfile(resp, "epub"))


def test_rejects_non_pdf_early():
    resp = _Resp([b"<html>not a pdf</html>" + b"x" * 5000])
    with pytest.raises(RuntimeError, match="Not a real"):
        asyncio.run(netfetch.stream_to_tempfile(resp, "pdf"))


def test_accepts_real_epub():
    resp = _Resp([b"PK\x03\x04" + b"x" * 2000])
    path = asyncio.run(netfetch.stream_to_tempfile(resp, "epub"))
    try:
        assert path and path.endswith(".epub")
    finally:
        if path:
            os.remove(path)


def test_unknown_ext_is_not_head_checked():
    # No magic table for mobi → no early check; any content is streamed through.
    resp = _Resp([b"whatever-bytes" + b"x" * 2000])
    path = asyncio.run(netfetch.stream_to_tempfile(resp, "mobi"))
    try:
        assert path and path.endswith(".mobi")
    finally:
        if path:
            os.remove(path)
