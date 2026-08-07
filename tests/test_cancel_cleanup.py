"""Regression lock for temp-file cleanup on cancellation (#8).

asyncio.CancelledError is a BaseException, not an Exception. The streaming helpers
used ``except Exception`` for cleanup, so a cancel mid-stream leaked the partial
temp file. These tests drive a cancel through the real helper and assert both that
cancellation propagates AND that no temp file is left behind.
"""

import asyncio
import os
import tempfile

import pytest

import anna_archive


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


def test_cancel_midstream_propagates_and_cleans_up():
    before = _librarian_temps()
    resp = _FakeResp(
        {"content-length": "999999"},
        chunks=[b"x" * 4096, b"y" * 4096, b"z" * 4096],
        raise_after=1,  # cancel after the first chunk is written
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(anna_archive._stream_resp_to_file(resp, "epub"))
    assert not (_librarian_temps() - before), "cancel mid-stream must not leak a temp file"


def test_normal_stream_returns_path_and_file_exists():
    resp = _FakeResp({"content-length": "8192"}, chunks=[b"x" * 4096, b"y" * 4096])
    path = asyncio.run(anna_archive._stream_resp_to_file(resp, "epub"))
    try:
        assert path and os.path.exists(path)
        assert os.path.getsize(path) == 8192
    finally:
        if path and os.path.exists(path):
            os.remove(path)
