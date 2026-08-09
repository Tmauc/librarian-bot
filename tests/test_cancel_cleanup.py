"""Regression lock for temp-file cleanup on cancellation (librarian.core.netfetch).

asyncio.CancelledError is a BaseException, not an Exception; the streaming helper
must still remove the partial temp file when a download is cancelled mid-stream.
"""

import asyncio
import os
import tempfile

import pytest

from librarian.core import netfetch


class _FakeResp:
    """Minimal stand-in for an httpx streaming response."""

    def __init__(self, headers, chunks, raise_after=None):
        self.headers = headers
        self._chunks = chunks
        self._raise_after = raise_after

    async def aiter_bytes(self, _size):
        emitted = 0
        for c in self._chunks:
            if self._raise_after is not None and emitted >= self._raise_after:
                raise asyncio.CancelledError()
            yield c
            emitted += 1
        if self._raise_after is not None:
            raise asyncio.CancelledError()


def _librarian_temps():
    return {f for f in os.listdir(tempfile.gettempdir()) if f.startswith("librarian_")}


# A valid EPUB starts with the ZIP local-file-header; the streaming helper now checks it on
# the leading bytes, so these generic cleanup/size tests must feed a well-formed header.
_EPUB_HEAD = b"PK\x03\x04"


def test_cancel_midstream_propagates_and_cleans_up():
    before = _librarian_temps()
    resp = _FakeResp({"content-length": "999999"}, chunks=[_EPUB_HEAD + b"x" * 4092, b"y" * 4096], raise_after=1)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(netfetch.stream_to_tempfile(resp, "epub"))
    assert not (_librarian_temps() - before), "cancel mid-stream must not leak a temp file"


def test_normal_stream_returns_path_and_file_exists():
    resp = _FakeResp({"content-length": "8192"}, chunks=[_EPUB_HEAD + b"x" * 4092, b"y" * 4096])
    path = asyncio.run(netfetch.stream_to_tempfile(resp, "epub"))
    try:
        assert path and os.path.exists(path)
        assert os.path.getsize(path) == 8192
    finally:
        if path and os.path.exists(path):
            os.remove(path)


def test_too_large_raises():
    resp = _FakeResp({"content-length": "999999"}, chunks=[_EPUB_HEAD + b"x" * 4092] + [b"x" * 4096] * 4)
    with pytest.raises(RuntimeError, match="too large"):
        asyncio.run(netfetch.stream_to_tempfile(resp, "epub", max_bytes=8192))
