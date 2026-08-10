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

_OMNI_SEP = re.compile(r"\s*/\s*")


def _omnibus_parts(label: str) -> list[str]:
    """Split a combined « Titre A / Titre B » omnibus label into its parts. Wikidata lists one
    row for two bound-together volumes (« Le Dragon des glaces /L'Homme noir »), and the slash may
    be spaced on EITHER side only. We require whitespace next to the slash — so « AC/DC », a
    fraction, or « N/A » stay whole — and each part to be substantial. Returns ``[label]`` when it
    is not an omnibus."""
    if not re.search(r"\s/|/\s", label):
        return [label]
    parts = [p.strip() for p in _OMNI_SEP.split(label) if len(p.strip()) >= 3]
    return parts if len(parts) > 1 else [label]


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


async def author_series(author: str, language: str = "fr") -> list[tuple[str, str, int]]:
    """Every series with ≥2 written-work volumes authored by ``author``, as
    ``(series_qid, label, volume_count)`` sorted by count desc. Labels come back in ``language``
    first — this is how « Cycle du Prophète Blanc » surfaces (a French cycle name Wikidata will NOT
    return from a « L'Assassin Royal » title search: that only yields the English « Farseer
    Trilogy »). Lets the flow offer the author's series so the user picks the right cycle. Soft-fails → []."""
    if not author.strip():
        return []
    langs = f"{language},en" if language and language != "en" else "en"
    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA) as client:
            resp = await client.get(_API, params={
                "action": "wbsearchentities", "search": author,
                "language": language or "en", "type": "item", "format": "json", "limit": 5,
            })
            resp.raise_for_status()
            aqids = [r["id"] for r in resp.json().get("search", []) if r.get("id", "").startswith("Q")]
            for aqid in aqids[:3]:
                query = (
                    "SELECT ?series ?seriesLabel (COUNT(DISTINCT ?vol) AS ?n) WHERE {"
                    f"  ?vol wdt:P50 wd:{aqid} . ?vol wdt:P179 ?series ."
                    f"  ?vol wdt:P31/wdt:P279* {_WRITTEN_WORK} ."
                    f'  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{langs}". }}'
                    "} GROUP BY ?series ?seriesLabel HAVING(?n >= 2) ORDER BY DESC(?n)"
                )
                r2 = await client.get(_SPARQL, params={"query": query, "format": "json"})
                r2.raise_for_status()
                out: list[tuple[str, str, int]] = []
                for b in r2.json()["results"]["bindings"]:
                    qid = b.get("series", {}).get("value", "").rsplit("/", 1)[-1]
                    label = b.get("seriesLabel", {}).get("value", "").strip()
                    n = int(b.get("n", {}).get("value", "0"))
                    if qid.startswith("Q") and label and not (label.startswith("Q") and label[1:].isdigit()):
                        out.append((qid, label, n))
                if out:
                    return out
            return []
    except Exception as e:
        logger.warning(f"Wikidata author_series failed for {author!r}: {e}")
        return []


async def volumes_of(qid: str, language: str = "fr", author: str = "") -> list[tuple[int | None, str]]:
    """Ordered volumes of a specific series ENTITY (by QID), skipping the wbsearch step — used once
    the user has picked a series from the author's list."""
    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA) as client:
            vols, _ = await _members(client, qid, language, author)
            return vols
    except Exception as e:
        logger.warning(f"Wikidata volumes_of failed for {qid}: {e}")
        return []


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
    ?s to qid itself OR the series qid belongs to.

    A series is linked to its volumes in either direction on Wikidata: volume → series via
    P179 (« part of the series »), or series → volume via P527 (« has part »). Some series use
    only one (Hex Hall has P527 but no P179), so we accept BOTH; the ordinal (P1545) can qualify
    either statement."""
    langs = f"{language},en" if language and language != "en" else "en"
    query = (
        "SELECT ?vol ?volLabel ?ord ?authorLabel WHERE {"
        f"  wd:{qid} wdt:P179? ?s ."                    # ?s = qid, or the series qid is part of
        "  { ?vol wdt:P179 ?s } UNION { ?s wdt:P527 ?vol }"  # volume→series OR series→volume
        f"  ?vol wdt:P31/wdt:P279* {_WRITTEN_WORK} ."   # books only — exclude films/games/…
        f"  {_author_filter(author)}"
        "  OPTIONAL { ?vol wdt:P50 ?author . }"
        "  OPTIONAL { ?vol p:P179 [ ps:P179 ?s ; pq:P1545 ?ord ] }"
        "  OPTIONAL { ?s p:P527 [ ps:P527 ?vol ; pq:P1545 ?ord ] }"
        f'  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{langs}". }}'
        "} ORDER BY xsd:integer(?ord)"
    )
    resp = await client.get(_SPARQL, params={"query": query, "format": "json"})
    resp.raise_for_status()
    rows: list[tuple[int | None, str, bool]] = []  # (ordinal, label, is_omnibus)
    omni_titles: set[str] = set()
    authors: list[str] = []
    for b in resp.json()["results"]["bindings"]:
        label = b.get("volLabel", {}).get("value", "").strip()
        # Skip no-label rows (Wikidata returns the Q-id).
        if not label or (label.startswith("Q") and label[1:].isdigit()):
            continue
        al = b.get("authorLabel", {}).get("value", "").strip()
        if al and not (al.startswith("Q") and al[1:].isdigit()):
            authors.append(al)
        ordv = b.get("ord", {}).get("value", "")
        ordn = int(ordv) if ordv.isdigit() else None
        parts = _omnibus_parts(label)  # « Tome A / Tome B » combined edition → [A, B]
        is_omni = len(parts) > 1
        rows.append((ordn, label, is_omni))
        if is_omni:
            omni_titles.update(p.lower() for p in parts)

    out: list[tuple[int | None, str]] = []
    seen: set[str] = set()
    if any(o for _, _, o in rows):
        # OMNIBUS-described series: the row ordinals are often GROUP numbers (3 intégrales of 3
        # books) that clash with a second, single-volume numbering on the standalone rows
        # (Les Aventuriers de la Mer). Trusting them mislabels/duplicates. Instead, expand the
        # omnibuses IN READING ORDER and renumber 1..N; a standalone row an omnibus already
        # lists is redundant (the omnibus places it better). An omnibus is a source of TITLES
        # to search individually — never downloaded as one file.
        for _, label, is_omni in sorted(rows, key=lambda r: (r[0] is None, r[0] or 0)):
            for part in (_omnibus_parts(label) if is_omni else [label]):
                key = part.lower()
                if not part or key in seen or (not is_omni and key in omni_titles):
                    continue
                seen.add(key)
                out.append((len(out) + 1, part))
    else:
        # Clean series: standalone rows with real per-volume ordinals — keep them (gaps and all).
        for ordn, label, _ in rows:
            if label.lower() in seen:
                continue
            seen.add(label.lower())
            out.append((ordn, label))
    author_str = Counter(authors).most_common(1)[0][0] if authors else ""
    return out, author_str
