"""Anna's Archive source (ported from anna_archive.py)."""

import logging
import re
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from librarian import config
from librarian.core.models import ProgressCallback, SearchResult
from librarian.core.netfetch import BROWSER_HEADERS, stream_to_tempfile
from librarian.core.security import _is_safe_url
from librarian.sources.base import Source

logger = logging.getLogger(__name__)

_MD5_RE = re.compile(r"^[a-f0-9]{32}$")
MAX_HTML_SIZE = 5 * 1024 * 1024  # 5 MB max for intermediate HTML pages


def _validate_md5(md5: str) -> bool:
    return bool(_MD5_RE.match(md5))


def _sanitize_ext(ext: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]", "", (ext or "").lower())[:10]
    return cleaned or "epub"


def _redact_url(url: str) -> str:
    """Strip query params from logged URLs (may contain auth tokens)."""
    try:
        p = urlparse(url)
        return urlunparse(p._replace(query="[redacted]" if p.query else ""))
    except Exception:
        return "[url]"


def _parse_size_from_text(text: str) -> int:
    """Extract a size in bytes from text like '2.3 MB' or '450 Ko'."""
    m = re.search(r"([\d.,]+)\s*(MB|KB|GB|Mo|Ko|Go)", text, re.IGNORECASE)
    if not m:
        return 0
    try:
        value = float(m.group(1).replace(",", "."))
        unit = m.group(2).upper()
        if unit in ("KB", "KO"):
            return int(value * 1024)
        if unit in ("MB", "MO"):
            return int(value * 1024 * 1024)
        if unit in ("GB", "GO"):
            return int(value * 1024 * 1024 * 1024)
    except ValueError:
        pass
    return 0


def _extract_download_link(html: str, source_url: str) -> str | None:
    """Find the real file link inside an intermediate HTML page (e.g. libgen.li/ads.php)."""
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href:
            continue
        lower = href.lower()
        if any(lower.endswith(ext) for ext in [".epub", ".pdf", ".mobi", ".azw3", ".fb2"]):
            return href if href.startswith("http") else urljoin(source_url, href)
        if "get.php" in lower and "md5" in lower:
            return href if href.startswith("http") else urljoin(source_url, href)
    return None


class AnnaArchiveSource(Source):
    name = "anna"

    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url if base_url is not None else config.ANNA_ARCHIVE_URL).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    # -- SSRF helpers (base_url is admin-configured, hence trusted) ----------
    def _is_trusted_url(self, url: str) -> bool:
        if self.base_url and url.startswith(self.base_url):
            return True
        return _is_safe_url(url)

    async def _check_redirect(self, response: httpx.Response) -> None:
        if response.is_redirect:
            location = str(response.headers.get("location", ""))
            if location and not self._is_trusted_url(location):
                raise ValueError(f"Redirect blocked (SSRF): {_redact_url(location)}")

    def _client(self, timeout: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=BROWSER_HEADERS,
            timeout=timeout,
            follow_redirects=True,
            event_hooks={"response": [self._check_redirect]},
        )

    # -- search -------------------------------------------------------------
    async def search(self, query: str) -> list[SearchResult]:
        if not self.enabled:
            return []
        async with self._client(15) as client:
            return await self._search_html(client, query)

    async def _search_html(self, client: httpx.AsyncClient, query: str) -> list[SearchResult]:
        try:
            resp = await client.get(
                f"{self.base_url}/search",
                params={"q": query, "lang": "", "content": "book_any", "ext": "epub,pdf,mobi"},
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"Anna's Archive HTML search failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        seen: dict[str, SearchResult] = {}
        for a in soup.select("a[href^='/md5/']"):
            href = a.get("href", "")
            md5 = href.split("/md5/")[-1].split("?")[0].strip()
            if not md5 or not _validate_md5(md5):
                continue
            text = a.get_text(" ", strip=True)
            if not text:
                continue
            if md5 in seen:
                if len(text) > len(seen[md5].title):
                    seen[md5].title = text[:120]
                continue
            ext = "epub"
            for e in ("epub", "pdf", "mobi"):
                if e in text.lower():
                    ext = e
                    break
            seen[md5] = SearchResult(
                source=self.name,
                title=text[:120],
                ext=_sanitize_ext(ext),
                size_bytes=_parse_size_from_text(text),
                is_torrent=False,
                ref={"md5": md5},
            )
            if len(seen) >= 10:
                break
        return list(seen.values())

    # -- download -----------------------------------------------------------
    async def download(
        self,
        result: SearchResult,
        on_progress: ProgressCallback | None = None,
        max_bytes: int = 0,
    ) -> str:
        md5 = result.ref.get("md5", "")
        ext = _sanitize_ext(result.ext)
        async with self._client(90) as client:
            links = await self._get_download_links(client, md5)
            for url in links:
                try:
                    if ".onion" in url or not self._is_trusted_url(url):
                        continue
                    logger.info(f"Trying download URL: {_redact_url(url)}")
                    async with client.stream("GET", url) as resp:
                        if resp.status_code != 200:
                            logger.warning(f"URL {_redact_url(url)} returned {resp.status_code}")
                            continue
                        ctype = resp.headers.get("content-type", "").split(";")[0].strip()
                        if "text/html" in ctype:
                            real_url = await self._resolve_html_link(resp, url)
                            if real_url:
                                result_path = await self._open_and_stream(
                                    client, real_url, ext, on_progress, max_bytes
                                )
                                if result_path:
                                    return result_path
                            logger.warning(f"URL {_redact_url(url)} was HTML, no real link found")
                            continue
                        path = await stream_to_tempfile(resp, ext, on_progress, max_bytes)
                        if path:
                            logger.info(f"Downloaded from {_redact_url(url)}")
                            return path
                except Exception as e:
                    logger.warning(f"URL {_redact_url(url)} failed: {e}")
        raise RuntimeError(f"All mirrors failed for md5={md5}")

    async def _get_download_links(self, client: httpx.AsyncClient, md5: str) -> list[str]:
        page_url = f"{self.base_url}/md5/{md5}"
        try:
            resp = await client.get(page_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            links = []
            for a in soup.select("a[href]"):
                href = a.get("href", "")
                text = a.get_text(strip=True).lower()
                if any(kw in text for kw in ["download", "télécharger", "get", "mirror", "libgen", "lol"]):
                    if href.startswith("http") and md5.lower() in href.lower() and _is_safe_url(href):
                        links.append(href)
                elif href.startswith("http") and md5.lower() in href.lower() and _is_safe_url(href):
                    links.append(href)
            links.append(f"{self.base_url}/slow_download/{md5}/0/0")
            logger.info(f"Found {len(links)} download links for md5={md5}")
            return links
        except Exception as e:
            logger.warning(f"Could not scrape book page for md5={md5}: {e}")
            return [f"{self.base_url}/slow_download/{md5}/0/0"]

    async def _resolve_html_link(self, resp, source_url: str) -> str | None:
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes(65536):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_HTML_SIZE:
                logger.warning("HTML page too large, skipping")
                break
        html = b"".join(chunks).decode("utf-8", errors="ignore")
        real_url = _extract_download_link(html, source_url)
        if real_url and not _is_safe_url(real_url):
            logger.warning(f"Real link rejected (SSRF): {_redact_url(real_url)}")
            return None
        if real_url:
            logger.info(f"Found real link in HTML: {_redact_url(real_url)}")
        return real_url

    async def _open_and_stream(self, client, url, ext, on_progress, max_bytes) -> str | None:
        try:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                ctype = resp.headers.get("content-type", "").split(";")[0].strip()
                if "text/html" in ctype:
                    return None
                return await stream_to_tempfile(resp, ext, on_progress, max_bytes)
        except Exception as e:
            logger.warning(f"Stream failed for {_redact_url(url)}: {e}")
            return None
