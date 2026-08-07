"""Prowlarr source (ported from prowlarr.py + downloader torrent/direct paths)."""

import logging

import httpx

from librarian import config
from librarian.core import watcher
from librarian.core.models import ProgressCallback, SearchResult
from librarian.core.netfetch import BROWSER_HEADERS, VALID_CONTENT_TYPES, stream_to_tempfile
from librarian.core.security import _is_safe_url
from librarian.sources.base import Source

logger = logging.getLogger(__name__)


def _guess_ext(title: str) -> str:
    title = (title or "").lower()
    for ext in ("epub", "pdf", "mobi", "azw3"):
        if ext in title:
            return ext
    return "epub"


async def _check_redirect(response: httpx.Response) -> None:
    if response.is_redirect:
        location = str(response.headers.get("location", ""))
        if location and not _is_safe_url(location):
            raise ValueError("Redirect blocked (SSRF)")


class ProwlarrSource(Source):
    name = "prowlarr"

    def __init__(self, base_url: str | None = None, api_key: str | None = None):
        self.base_url = (base_url if base_url is not None else config.PROWLARR_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.PROWLARR_API_KEY

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _api(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, headers={"X-Api-Key": self.api_key}, timeout=20
        )

    # -- search -------------------------------------------------------------
    async def search(self, query: str) -> list[SearchResult]:
        if not self.enabled:
            return []
        params = {"query": query, "categories[]": ["7000", "7020"], "type": "search"}
        async with self._api() as client:
            try:
                resp = await client.get("/api/v1/search", params=params)
                resp.raise_for_status()
                items = resp.json()
            except Exception as e:
                logger.error(f"Prowlarr search failed: {e}")
                return []

        results = []
        for item in items:
            dl_url = item.get("downloadUrl") or ""
            guid = item.get("guid") or ""
            if not dl_url and not guid:
                continue
            magnet = item.get("magnetUrl") or ""
            is_torrent = (
                dl_url.endswith(".torrent")
                or bool(magnet)
                or item.get("downloadProtocol", "").lower() == "torrent"
            )
            title = item.get("title") or ""
            results.append(
                SearchResult(
                    source=self.name,
                    title=title,
                    ext=_guess_ext(title),
                    size_bytes=item.get("size") or 0,
                    is_torrent=is_torrent,
                    ref={
                        "guid": guid,
                        "indexer_id": item.get("indexerId") or 0,
                        "download_url": dl_url,
                        "magnet_url": magnet,
                    },
                )
            )
        return results

    # -- download -----------------------------------------------------------
    async def download(
        self,
        result: SearchResult,
        on_progress: ProgressCallback | None = None,
        max_bytes: int = 0,
    ) -> str:
        if result.is_torrent:
            return await self._download_torrent(result)
        return await self._download_direct(result, on_progress, max_bytes)

    async def _download_direct(self, result, on_progress, max_bytes) -> str:
        url = result.ref.get("download_url", "")
        if not _is_safe_url(url):
            raise ValueError("URL rejected (SSRF protection)")
        ext = result.ext or "epub"
        async with httpx.AsyncClient(
            headers=BROWSER_HEADERS, timeout=60, follow_redirects=True,
            event_hooks={"response": [_check_redirect]},
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "").split(";")[0].strip()
                if ctype and ctype not in VALID_CONTENT_TYPES:
                    raise RuntimeError(f"Unexpected content-type: {ctype!r}")
                path = await stream_to_tempfile(resp, ext, on_progress, max_bytes)
                if not path:
                    raise RuntimeError("Empty download")
                return path

    async def _download_torrent(self, result) -> str:
        await self._grab(result.ref.get("indexer_id", 0), result.ref.get("guid", ""))
        return await watcher.wait_for_file(
            result.title, config.BOOKS_DOWNLOAD_PATH, config.DOWNLOAD_TIMEOUT_MINUTES
        )

    async def _grab(self, indexer_id: int, guid: str) -> None:
        payload = {"guid": guid, "indexerId": indexer_id}
        async with self._api() as client:
            try:
                resp = await client.post("/api/v1/download", json=payload)
                resp.raise_for_status()
                logger.info(f"Prowlarr grab successful for guid={guid}")
            except Exception as e:
                logger.error(f"Prowlarr grab failed: {e}")
                raise
