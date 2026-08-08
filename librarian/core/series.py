"""Series enumeration via Wikidata — the canonical, ordered volume list of a series.

A small local LLM cannot reliably know a niche series' volumes; Wikidata can. Given a
series name we resolve the series entity and return its ordered volume titles (P179
"part of the series" + P1545 "series ordinal"). Free, no key. Fails soft → [].
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "librarian-bot/2.x (series lookup)"}
_API = "https://www.wikidata.org/w/api.php"
_SPARQL = "https://query.wikidata.org/sparql"


async def volumes(name: str, language: str = "fr") -> list[tuple[int | None, str]]:
    """Return the series' ordered volumes as ``(ordinal, title)`` pairs (or []).

    ``ordinal`` is the volume number (P1545) when Wikidata has it, else None.
    Labels are requested in ``language`` first (fallback English).
    """
    if not name.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA) as client:
            resp = await client.get(
                _API,
                params={
                    "action": "wbsearchentities", "search": name,
                    "language": language or "en", "format": "json", "limit": 5,
                },
            )
            resp.raise_for_status()
            candidates = [r["id"] for r in resp.json().get("search", []) if r.get("id", "").startswith("Q")]

            best: list[tuple[int | None, str]] = []
            for qid in candidates:
                vols = await _parts(client, qid, language)
                if len(vols) > len(best):
                    best = vols
                if len(best) >= 3:  # a real series — good enough, stop early
                    break
            return best
    except Exception as e:
        logger.warning(f"Wikidata series lookup failed for {name!r}: {e}")
        return []


async def _parts(client: httpx.AsyncClient, qid: str, language: str) -> list[tuple[int | None, str]]:
    langs = f"{language},en" if language and language != "en" else "en"
    query = (
        "SELECT ?vol ?volLabel ?ord WHERE {"
        f"  ?vol wdt:P179 wd:{qid} ."
        f"  OPTIONAL {{ ?vol p:P179 [ ps:P179 wd:{qid} ; pq:P1545 ?ord ] }}"
        f'  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{langs}". }}'
        "} ORDER BY xsd:integer(?ord)"
    )
    resp = await client.get(_SPARQL, params={"query": query, "format": "json"})
    resp.raise_for_status()
    out: list[tuple[int | None, str]] = []
    seen: set[str] = set()
    for b in resp.json()["results"]["bindings"]:
        label = b.get("volLabel", {}).get("value", "").strip()
        # skip volumes with no human label (Wikidata returns the Q-id then) + duplicates
        if not label or (label.startswith("Q") and label[1:].isdigit()) or label.lower() in seen:
            continue
        seen.add(label.lower())
        ordv = b.get("ord", {}).get("value", "")
        out.append((int(ordv) if ordv.isdigit() else None, label))
    return out
