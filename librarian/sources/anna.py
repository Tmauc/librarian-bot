"""Anna's Archive source (ported from anna_archive.py)."""

import asyncio
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

# External mirror hosts Anna links out to (books work through these).
_MIRROR_HOSTS = ("libgen", "booksdl", "books.ms", "library.lol", "1lib", "z-lib", "zlib")
# Mirrors that reliably waste our time (blocked in FR → connect timeout, or always-503):
# keep them, but try them after the good ones.
_DEPRIORITIZE = ("libgen.is", "z-library", "z-lib", "1lib", "libgen.rs")


def _link_rank(url: str) -> int:
    """Order download links: good external mirrors first (0), known-slow ones next (1),
    Anna's own membership-gated fast/slow_download last (2) — they 403 without an account."""
    u = url.lower()
    if "/fast_download/" in u or "/slow_download/" in u:
        return 2
    return 1 if any(bad in u for bad in _DEPRIORITIZE) else 0


def _is_download_link(url: str, md5: str, base_host: str) -> bool:
    """Whether a scraped link is a real download route (not a search/account page).

    Anna keeps changing this page: the working libgen mirror is now
    ``libgen.li/file.php?id=<num>`` — NO md5 in the URL — so we must accept mirror hosts
    and download endpoints, not only links that literally contain the md5."""
    parsed = urlparse(url.lower())
    host, path = parsed.netloc, parsed.path
    if base_host and base_host in host:  # Anna's own domain: only its download endpoints
        return "/fast_download/" in path or "/slow_download/" in path
    if any(m in host for m in _MIRROR_HOSTS):
        return True
    return any(s in url.lower() for s in ("get.php", "file.php", "ads.php")) or md5.lower() in url.lower()


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


def _extract_card_meta(anchor) -> dict:
    """From a result's title anchor, pull display metadata off the surrounding card.

    Structure (Anna's Archive search result):
      metadata line (anchor.parent): <div>filename</div> <a>title</a> <a>author</a> <a>publisher, …, year</a>
      card (anchor.parent.parent): + a description div + a technical div
                                   ("English [en] · EPUB · 1.8MB · 2003 · …")
    Best-effort — any field that can't be found stays "".
    """
    meta = {"author": "", "year": "", "language": "", "ext": "", "cover": "", "description": "", "size_bytes": 0}
    line = anchor.parent
    card = line.parent if line is not None else None

    if line is not None:
        links = line.find_all("a")  # [title, author, publisher]
        if len(links) >= 2:
            meta["author"] = links[1].get_text(" ", strip=True)[:80]

    if card is not None:
        description = ""
        for div in card.find_all("div"):
            t = div.get_text(" ", strip=True)
            if not t:
                continue
            # Technical line, e.g. "English [en] · EPUB · 1.8MB · 2003 · 📕 Book (fiction) · …"
            if "·" in t and re.search(r"\d[.,\d]*\s*(MB|KB|GB|Mo|Ko|Go)", t, re.I):
                mlang = re.match(r"([A-Za-zÀ-ÿ]+)", t)
                if mlang:
                    meta["language"] = mlang.group(1)
                myear = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", t)
                if myear:
                    meta["year"] = myear.group(1)
                mext = re.search(r"\b(EPUB|PDF|MOBI|AZW3|FB2)\b", t, re.I)
                if mext:
                    meta["ext"] = mext.group(1).lower()
                meta["size_bytes"] = _parse_size_from_text(t)
            elif "·" not in t and "score:" not in t and "/" not in t[:40] and len(t) > len(description):
                description = t
        meta["description"] = description[:600]

    # Cover: climb to the nearest ancestor holding an <img>, but stop before ascending
    # into a container that describes more than one result (avoids a neighbour's cover).
    node = anchor
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        md5s = {x.get("href", "").split("/md5/")[-1].split("?")[0] for x in node.select("a[href^='/md5/']")}
        if len(md5s) > 1:
            break
        img = node.select_one("img")
        if img is not None:
            src = img.get("src") or img.get("data-src") or ""
            if src.startswith("http"):
                meta["cover"] = src
            break
    return meta


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
            # Cap the CONNECT phase hard: a dead/blocked mirror (e.g. libgen.is in FR)
            # otherwise hangs ~75s exhausting every A-record before we move on.
            timeout=httpx.Timeout(timeout, connect=8),
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
        resp = None
        for attempt in range(4):  # Anna rate-limits big series (429) → back off and retry
            try:
                resp = await client.get(
                    f"{self.base_url}/search",
                    params={"q": query, "lang": "", "content": "book_any", "ext": "epub,pdf,mobi"},
                )
                if resp.status_code == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                    resp = None
                    continue
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt == 3:
                    logger.error(f"Anna's Archive HTML search failed: {e}")
                    return []
                await asyncio.sleep(1.5 * (attempt + 1))
        if resp is None:
            logger.warning(f"Anna search gave up (rate-limited) for {query!r}")
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
            meta = _extract_card_meta(a)
            seen[md5] = SearchResult(
                source=self.name,
                title=text[:120],
                author=meta["author"],
                ext=_sanitize_ext(meta["ext"] or ext),
                size_bytes=meta["size_bytes"] or _parse_size_from_text(text),
                is_torrent=False,
                year=meta["year"],
                language=meta["language"],
                cover=meta["cover"],
                description=meta["description"],
                ref={"md5": md5},
            )
            if len(seen) >= 60:  # gather generously; search_service dedups + caps at MAX_RESULTS
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
        slow = f"{self.base_url}/slow_download/{md5}/0/0"
        base_host = urlparse(self.base_url).netloc
        try:
            resp = await client.get(page_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            links: list[str] = []
            seen: set[str] = set()
            for a in soup.select("a[href]"):
                href = (a.get("href") or "").strip()
                if not href or href.startswith(("#", "javascript:")):
                    continue
                full = href if href.startswith("http") else urljoin(f"{self.base_url}/", href)
                if full in seen or not _is_safe_url(full) or not _is_download_link(full, md5, base_host):
                    continue
                seen.add(full)
                links.append(full)
            # Good external mirrors first, Anna's membership-gated endpoints last.
            links.sort(key=_link_rank)
            if slow not in seen:  # always keep the last-resort slow_download
                links.append(slow)
            logger.info(f"Found {len(links)} download links for md5={md5}")
            return links
        except Exception as e:
            logger.warning(f"Could not scrape book page for md5={md5}: {e}")
            return [slow]

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

    # -- detail card --------------------------------------------------------
    async def details(self, result: SearchResult) -> dict:
        """Fetch the book page for a fuller description + cover (for the detail card)."""
        md5 = result.ref.get("md5", "")
        if not self.enabled or not md5:
            return {}
        out: dict = {}
        try:
            async with self._client(20) as client:
                resp = await client.get(f"{self.base_url}/md5/{md5}")
                resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            m = soup.find("meta", attrs={"name": "description"})
            if m and m.get("content"):
                # content is "Author\n\nDescription\n\nPublisher" — keep the longest chunk.
                chunks = [c.strip() for c in str(m["content"]).split("\n") if c.strip()]
                if chunks:
                    out["description"] = max(chunks, key=len)[:800]
            og = soup.find("meta", attrs={"property": "og:image"})
            if og and str(og.get("content", "")).startswith("http"):
                out["cover"] = str(og["content"])
        except Exception as e:
            logger.warning(f"Anna details failed for md5={md5}: {e}")
        return out
