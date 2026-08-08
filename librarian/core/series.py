"""Series enumeration via Wikidata — the canonical, ordered volume list of a series.

A small local LLM cannot reliably know a niche series' volumes; Wikidata can. Given a
series name we resolve the series entity and return its ordered volume titles (P179
"part of the series" + P1545 "series ordinal"). Free, no key. Fails soft → [].
"""

import logging
import re
from collections import Counter

import httpx

logger = logging.getLogger(__name__)

# A Wikidata "series" (P179) groups everything: books, but also the film adaptations,
# video games, etc. We only want the BOOKS, so we require each volume to be a written
# work (literary work / book / novel via subclass-of). This is what stops e.g. the
# Hunger Games *films* — one of which was split into « La Révolte partie 1 / partie 2 »
# — from being offered as tomes. Q7725634 = literary work.
_WRITTEN_WORK = "wd:Q7725634"

_UA = {"User-Agent": "librarian-bot/2.x (series lookup)"}
_API = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"


async def volumes(name: str, language: str = "fr", author: str = "") -> list[tuple[int | None, str]]:
    """Ordered volumes only (see :func:`resolve`)."""
    _, vols = await resolve(name, language, author)
    return vols


async def resolve(name: str, language: str = "fr", author: str = "") -> tuple[str, list[tuple[int | None, str]]]:
    """Resolve a series to ``(canonical_author, ordered_volumes)``.

    ``ordinal`` is the volume number (P1545) when Wikidata has it, else None. Labels are
    requested in ``language`` first (fallback English). ``canonical_author`` is the series'
    Wikidata author (P50) — stable across runs, unlike a per-download best-guess. ``author``
    (optional) disambiguates same-named series; if it matches nothing we ignore it.
    """
    if not name.strip():
        return "", []
    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA) as client:
            resp = await client.get(
                _API,
                params={
                    # Look past the top hits: for an ambiguous name (« Dune ») the film /
                    # game / landform rank above the novel we actually want.
                    "action": "wbsearchentities", "search": name,
                    "language": language or "en", "format": "json", "limit": 12,
                },
            )
            resp.raise_for_status()
            candidates = [r["id"] for r in resp.json().get("search", []) if r.get("id", "").startswith("Q")]

            best_author, best = await _best_over(client, candidates, language, author)
            if not best and author:  # the author didn't match Wikidata → drop the filter
                best_author, best = await _best_over(client, candidates, language, "")
            return best_author, best
    except Exception as e:
        logger.warning(f"Wikidata series lookup failed for {name!r}: {e}")
        return "", []


async def _best_over(client, candidates, language, author) -> tuple[str, list[tuple[int | None, str]]]:
    """The longest volume list (+ its author) across the candidates (stops at ≥ 3)."""
    best: list[tuple[int | None, str]] = []
    best_author = ""
    for qid in candidates:
        vols, vol_author = await _members(client, qid, language, author)
        if len(vols) > len(best):
            best, best_author = vols, vol_author
        if len(best) >= 3:  # a real series — good enough, stop early
            break
    return best_author, best


def _author_filter(author: str) -> str:
    """A SPARQL fragment requiring the volume's author (P50) label to contain every word
    of ``author`` (so « Frank Herbert » ≠ « Brian Herbert »). Empty if no author."""
    words = [w for w in re.split(r"\s+", author.strip()) if len(w) > 1]
    if not words:
        return ""
    conds = " && ".join(
        f'CONTAINS(LCASE(STR(?authL)), "{w.lower().replace(chr(92), "").replace(chr(34), "")}")'
        for w in words
    )
    return f"?vol wdt:P50 ?auth . ?auth rdfs:label ?authL . FILTER({conds})"


async def _members(client, qid, language, author="") -> tuple[list[tuple[int | None, str]], str]:
    """Ordered book volumes (+ the series' canonical author) for a candidate, in ONE query.
    ``qid`` may BE the series, or — for an ambiguous name like « Dune » where wbsearch
    returns the first novel — a book *part of* the series: ``wd:qid wdt:P179? ?s`` resolves
    ?s to qid itself OR the series qid belongs to."""
    langs = f"{language},en" if language and language != "en" else "en"
    query = (
        "SELECT ?vol ?volLabel ?ord ?authorLabel WHERE {"
        f"  wd:{qid} wdt:P179? ?s ."                    # ?s = qid, or the series qid is part of
        "  ?vol wdt:P179 ?s ."
        f"  ?vol wdt:P31/wdt:P279* {_WRITTEN_WORK} ."   # books only — exclude films/games/…
        f"  {_author_filter(author)}"
        "  OPTIONAL { ?vol wdt:P50 ?author . }"
        "  OPTIONAL { ?vol p:P179 [ ps:P179 ?s ; pq:P1545 ?ord ] }"
        f'  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{langs}". }}'
        "} ORDER BY xsd:integer(?ord)"
    )
    resp = await client.get(_SPARQL, params={"query": query, "format": "json"})
    resp.raise_for_status()
    out: list[tuple[int | None, str]] = []
    seen: set[str] = set()
    authors: list[str] = []
    for b in resp.json()["results"]["bindings"]:
        label = b.get("volLabel", {}).get("value", "").strip()
        # Skip: no human label (Wikidata returns the Q-id), duplicates, and OMNIBUS editions
        # (« Tome A / Tome B ») — combined volumes that would download as one confusing file
        # (e.g. L'Assassin Royal); the individual volumes are listed separately anyway.
        if (not label or (label.startswith("Q") and label[1:].isdigit())
                or label.lower() in seen or " / " in label):
            continue
        seen.add(label.lower())
        ordv = b.get("ord", {}).get("value", "")
        out.append((int(ordv) if ordv.isdigit() else None, label))
        al = b.get("authorLabel", {}).get("value", "").strip()
        if al and not (al.startswith("Q") and al[1:].isdigit()):
            authors.append(al)
    author_str = Counter(authors).most_common(1)[0][0] if authors else ""
    return out, author_str
