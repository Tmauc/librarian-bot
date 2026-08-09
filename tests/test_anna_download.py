"""Anna download loop: it must FAIL FAST on membership/DDoS-gated books instead of
grinding through every partner-index × tracking-query variant (was ~2.5 min/book).

Regression guard for the annas-archive.gl reality: fast_download 302s to
/fast_download_not_member (no membership) and slow_download 403s behind DDoS-Guard —
so once the first of each type fails that way, the rest are skipped.
"""

import asyncio

import pytest

from librarian.core.models import SearchResult
from librarian.sources import anna

MD5 = "a" * 32


def _page_html() -> str:
    # What Anna's md5 page really looks like: 28 fast partner-indices, each decorated
    # with 4 tracking-query variants, plus 8 slow indices. All gated, all useless.
    parts = []
    for i in range(28):
        for q in ("", "?viewer=1", "?no_redirect=1", "?short=1"):
            parts.append(f'<a href="/fast_download/{MD5}/0/{i}{q}">f</a>')
    for i in range(8):
        parts.append(f'<a href="/slow_download/{MD5}/0/{i}">s</a>')
    return "<html><body>" + "".join(parts) + "</body></html>"


class _Resp:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _StreamCtx:
    def __init__(self, url, attempts):
        self.url = url
        self._attempts = attempts
        self.status_code = 200
        self.headers = {}

    async def __aenter__(self):
        self._attempts.append(self.url)
        if "/fast_download/" in self.url:  # no membership → SSRF-blocked redirect
            raise ValueError("Redirect blocked (SSRF): /fast_download_not_member")
        if "/slow_download/" in self.url:  # DDoS-Guard
            self.status_code = 403
        return self

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, attempts):
        self._attempts = attempts

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, *a, **k):
        return _Resp(_page_html())

    def stream(self, method, url, *a, **k):
        return _StreamCtx(url, self._attempts)


def test_get_download_links_collapses_variants_and_caps(monkeypatch):
    monkeypatch.setattr(anna, "_is_safe_url", lambda u: True)
    src = anna.AnnaArchiveSource("https://annas-archive.gl")
    links = asyncio.run(src._get_download_links(_FakeClient([]), MD5))
    fast = [u for u in links if "/fast_download/" in u]
    slow = [u for u in links if "/slow_download/" in u]
    assert len(fast) <= 2  # 112 variants collapsed + capped
    assert len(slow) <= 3  # 8 indices capped (+ the appended last-resort)
    assert all("?" not in u for u in fast + slow)  # tracking-query variants stripped


def test_download_short_circuits_gated_endpoints(monkeypatch):
    monkeypatch.setattr(anna, "_is_safe_url", lambda u: True)
    attempts: list[str] = []
    src = anna.AnnaArchiveSource("https://annas-archive.gl")
    monkeypatch.setattr(src, "_client", lambda timeout: _FakeClient(attempts))

    with pytest.raises(RuntimeError, match="All mirrors failed"):
        asyncio.run(src.download(SearchResult("anna", "x", "epub", ref={"md5": MD5})))

    # The first not-member fast and the first 403 slow are enough; the rest are skipped.
    assert sum("/fast_download/" in u for u in attempts) == 1
    assert sum("/slow_download/" in u for u in attempts) == 1
