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


async def volumes(name: str) -> list[str]:
    """Return the ordered volume titles of the series named ``name`` (or [])."""
    if not name.strip():
        return []
    try:
        async with httpx.AsyncClient(timeout=25, headers=_UA) as client:
            resp = await client.get(
                _API,
                params={
                    "action": "wbsearchentities", "search": name,
                    "language": "fr", "format": "json", "limit": 5,
                },
            )
            resp.raise_for_status()
            candidates = [r["id"] for r in resp.json().get("search", []) if r.get("id", "").startswith("Q")]

            best: list[str] = []
            for qid in candidates:
                vols = await _parts(client, qid)
                if len(vols) > len(best):
                    best = vols
                if len(best) >= 3:  # a real series — good enough, stop early
                    break
            return best
    except Exception as e:
        logger.warning(f"Wikidata series lookup failed for {name!r}: {e}")
        return []


async def _parts(client: httpx.AsyncClient, qid: str) -> list[str]:
    query = (
        "SELECT ?vol ?volLabel ?ord WHERE {"
        f"  ?vol wdt:P179 wd:{qid} ."
        f"  OPTIONAL {{ ?vol p:P179 [ ps:P179 wd:{qid} ; pq:P1545 ?ord ] }}"
        '  SERVICE wikibase:label { bd:serviceParam wikibase:language "fr,en". }'
        "} ORDER BY xsd:integer(?ord)"
    )
    resp = await client.get(_SPARQL, params={"query": query, "format": "json"})
    resp.raise_for_status()
    out = []
    for b in resp.json()["results"]["bindings"]:
        label = b.get("volLabel", {}).get("value", "").strip()
        # skip volumes with no human label (Wikidata returns the Q-id then)
        if label and not (label.startswith("Q") and label[1:].isdigit()):
            out.append(label)
    return out
