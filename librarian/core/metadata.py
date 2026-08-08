"""Rewrite a downloaded book's metadata (title/author/series+number/language/year/
cover) so it lands clean on the reader — proper series grouping on a Kobo/Kindle,
no "Untitled" junk from the source filename.

Source of truth, in order of trust:
  1. what the caller already knows — Wikidata gives the canonical series name +
     volume number + title (see clients/flow batch), Anna gives author/year/language/
     cover — passed in via ``hint`` and the ``SearchResult``;
  2. Open Library (free, keyless) fills the gaps (ISBN, a cover, a year).

Writing, in order of quality:
  1. Calibre's ``ebook-meta`` when available (handles EPUB packaging + cover embedding
     correctly, and MOBI/AZW3 too) — see core/calibre for how it's located;
  2. else a careful in-place OPF edit for EPUB (text fields only; we skip the cover to
     avoid the classic half-embedded "grey thumbnail").

Everything here is best-effort: any failure leaves the file untouched and returns
False — a book with imperfect metadata still beats no book.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from xml.sax.saxutils import escape

import httpx

from librarian.core import calibre
from librarian.core.netfetch import BROWSER_HEADERS
from librarian.core.security import _is_safe_url

logger = logging.getLogger(__name__)

_OL_SEARCH = "https://openlibrary.org/search.json"
_OL_COVER = "https://covers.openlibrary.org/b/id/{}-L.jpg"

# Display name / prefix → BCP-47 code (Anna reports "Français"/"English"; a plan
# already carries a code like "fr").
_LANG_CODES = {
    "fr": "fr", "franç": "fr", "francais": "fr", "french": "fr",
    "en": "en", "english": "en", "anglais": "en",
    "es": "es", "español": "es", "espagnol": "es", "spanish": "es",
    "de": "de", "deutsch": "de", "allemand": "de", "german": "de",
    "it": "it", "italien": "it", "italiano": "it", "italian": "it",
}


@dataclass
class BookMeta:
    title: str = ""
    author: str = ""
    series: str = ""
    index: int | None = None
    language: str = ""       # BCP-47 code, e.g. "fr"
    year: str = ""
    isbn: str = ""
    description: str = ""
    cover_url: str = ""


def _lang_code(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    for prefix, code in _LANG_CODES.items():
        if v.startswith(prefix):
            return code
    return v[:2]


_AUTHOR_CUT = re.compile(r"\s*(?:;|/|\btrad\b|\btraduit\b|\btranslated\b).*$", re.IGNORECASE)


def clean_author(author: str) -> str:
    """Keep the primary author, dropping translators / secondary contributors that vary
    per edition (« Suzanne Collins; trad. … par Guillaume Fournier » → « Suzanne Collins »)."""
    return _AUTHOR_CUT.sub("", author or "").strip().strip(",").strip()


def _clean_title(title: str) -> str:
    """Anna titles often repeat the tail (« … : Le feu dans le ciel : Le feu d »).
    Collapse consecutive duplicated « : » segments."""
    parts = [p.strip() for p in (title or "").split(" : ") if p.strip()]
    out: list[str] = []
    for p in parts:
        if not out or out[-1].lower() != p.lower():
            out.append(p)
    return " : ".join(out)[:200]


def _compose_title(vol_title: str, series: str, index: int | None) -> str:
    """The canonical display title. Series volume → « Série - TX : titre du tome »
    (dropping the « : titre » when it just repeats the series name); standalone → the
    title alone."""
    vol = (vol_title or "").strip()
    if not series:
        return vol
    tag = f"{series} - T{index:02d}" if index is not None else series  # zero-padded → T01, T02…
    if vol and vol.lower() != series.lower():
        return f"{tag} : {vol}"
    return tag


def build(result, hint: BookMeta | None = None) -> BookMeta:
    """Assemble a BookMeta from what we already know (hint wins over the raw result)."""
    hint = hint or BookMeta()
    vol_title = hint.title or _clean_title(getattr(result, "title", ""))
    return BookMeta(
        # A hint (canonical, from Wikidata/series) wins so a series stays uniform; the
        # raw per-edition author is cleaned of translators either way.
        title=_compose_title(vol_title, hint.series, hint.index),
        author=clean_author(hint.author or getattr(result, "author", "")),
        series=hint.series,
        index=hint.index,
        language=hint.language or _lang_code(getattr(result, "language", "")),
        year=getattr(result, "year", ""),
        description=getattr(result, "description", ""),
        cover_url=getattr(result, "cover", ""),
    )


async def enrich(meta: BookMeta) -> BookMeta:
    """Fill missing ISBN / year / cover from Open Library (free, keyless). Best-effort."""
    if meta.isbn and meta.year and meta.cover_url:
        return meta
    if not meta.title:
        return meta
    params = {"title": meta.title, "limit": 1, "fields": "isbn,cover_i,first_publish_year,author_name"}
    if meta.author:
        params["author"] = meta.author
    try:
        async with httpx.AsyncClient(timeout=15, headers=BROWSER_HEADERS) as client:
            resp = await client.get(_OL_SEARCH, params=params)
            resp.raise_for_status()
            docs = resp.json().get("docs", [])
    except Exception as e:
        logger.warning(f"Open Library enrich failed for {meta.title!r}: {e}")
        return meta
    if not docs:
        return meta
    doc = docs[0]
    if not meta.isbn and doc.get("isbn"):
        meta.isbn = doc["isbn"][0]
    if not meta.year and doc.get("first_publish_year"):
        meta.year = str(doc["first_publish_year"])
    if not meta.author and doc.get("author_name"):
        meta.author = ", ".join(doc["author_name"][:2])
    if not meta.cover_url and doc.get("cover_i"):
        meta.cover_url = _OL_COVER.format(doc["cover_i"])
    return meta


async def prepare(result, hint: BookMeta | None = None) -> BookMeta:
    """Build + enrich a BookMeta (no writing). The caller reuses it both to tag the
    file (``apply``) and to file it into folders (destinations)."""
    return await enrich(build(result, hint))


async def tag(path: str, ext: str, result, hint: BookMeta | None = None) -> bool:
    """Build + enrich + write metadata into ``path`` in place. Best-effort → applied?"""
    return await apply(path, ext, await prepare(result, hint))


async def apply(path: str, ext: str, meta: BookMeta) -> bool:
    """Write ``meta`` into the file. Calibre when present, else an OPF edit for EPUB."""
    tool = calibre.tool("ebook-meta")
    if tool:
        return await _apply_calibre(tool, path, meta)
    if ext == "epub":
        return await asyncio.get_event_loop().run_in_executor(None, _apply_opf_sync, path, meta)
    return False


# -- Calibre path -----------------------------------------------------------
async def _fetch_cover(url: str) -> str | None:
    if not url or not _is_safe_url(url):
        return None
    try:
        async with httpx.AsyncClient(timeout=20, headers=BROWSER_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            if "image" not in resp.headers.get("content-type", ""):
                return None
            suffix = ".png" if "png" in resp.headers.get("content-type", "") else ".jpg"
            fd, cover_path = tempfile.mkstemp(suffix=suffix, prefix="librarian_cover_")
            with os.fdopen(fd, "wb") as f:
                f.write(resp.content)
            return cover_path
    except Exception as e:
        logger.warning(f"Cover fetch failed: {e}")
        return None


async def _apply_calibre(tool: str, path: str, meta: BookMeta) -> bool:
    cover_path = await _fetch_cover(meta.cover_url)
    args = [tool, path]
    if meta.title:
        args += ["--title", meta.title]
    if meta.author:
        args += ["--authors", meta.author]
    if meta.series:
        args += ["--series", meta.series]
        if meta.index is not None:  # --index is meaningless without a series
            args += ["--index", str(meta.index)]
    if meta.language:
        args += ["--language", meta.language]
    if meta.year:
        args += ["--date", meta.year]
    if meta.isbn:
        args += ["--isbn", meta.isbn]
    if meta.description:
        args += ["--comments", meta.description]
    if cover_path:
        args += ["--cover", cover_path]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(f"ebook-meta failed: {err.decode('utf-8', 'ignore')[:300]}")
            return False
        return True
    finally:
        if cover_path:
            with contextlib.suppress(Exception):
                os.remove(cover_path)


# -- OPF fallback (EPUB, text fields only) ----------------------------------
def _find_opf_name(z: zipfile.ZipFile) -> str | None:
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", "ignore")
        m = re.search(r'full-path="([^"]+\.opf)"', container)
        if m:
            return m.group(1)
    except Exception:
        pass
    return next((n for n in z.namelist() if n.lower().endswith(".opf")), None)


def _set_dc(opf: str, tag: str, value: str) -> str:
    """Replace the first <dc:tag>…</dc:tag> content, or inject one into <metadata>."""
    if not value:
        return opf
    esc = escape(value)
    pattern = re.compile(rf"(<dc:{tag}\b[^>]*>).*?(</dc:{tag}>)", re.DOTALL)
    if pattern.search(opf):
        return pattern.sub(rf"\g<1>{esc}\g<2>", opf, count=1)
    return re.sub(r"(</metadata>)", f"<dc:{tag}>{esc}</dc:{tag}>\\1", opf, count=1)


def _set_calibre_meta(opf: str, name: str, content: str) -> str:
    if not content:
        return opf
    esc = escape(content, {'"': "&quot;"})
    pattern = re.compile(rf'<meta[^>]*name="{re.escape(name)}"[^>]*/?>')
    tag = f'<meta name="{name}" content="{esc}"/>'
    if pattern.search(opf):
        return pattern.sub(tag, opf, count=1)
    return re.sub(r"(</metadata>)", f"{tag}\\1", opf, count=1)


def _patch_opf(opf: str, meta: BookMeta) -> str:
    opf = _set_dc(opf, "title", meta.title)
    opf = _set_dc(opf, "creator", meta.author)
    opf = _set_dc(opf, "language", meta.language)
    opf = _set_dc(opf, "date", meta.year)
    opf = _set_calibre_meta(opf, "calibre:series", meta.series)
    if meta.index is not None:
        opf = _set_calibre_meta(opf, "calibre:series_index", str(meta.index))
    return opf


def _apply_opf_sync(path: str, meta: BookMeta) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            opf_name = _find_opf_name(z)
            if not opf_name:
                return False
            data = {n: z.read(n) for n in names}
        data[opf_name] = _patch_opf(data[opf_name].decode("utf-8", "ignore"), meta).encode("utf-8")

        tmp = path + ".tmp"
        with zipfile.ZipFile(tmp, "w") as z:
            # EPUB OCF requires 'mimetype' to be the first entry AND stored uncompressed.
            if "mimetype" in data:
                z.writestr(zipfile.ZipInfo("mimetype"), data.pop("mimetype"), compress_type=zipfile.ZIP_STORED)
            for name, blob in data.items():
                z.writestr(name, blob, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning(f"OPF metadata edit failed: {e}")
        with contextlib.suppress(Exception):
            os.remove(path + ".tmp")
        return False
