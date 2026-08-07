"""Intelligence layer: turn a free-text request into a search Plan via a local LLM.

Talks to an Ollama-compatible server (``/api/generate`` with JSON output). Entirely
optional: if ``LLM_MODEL`` is unset or the server is unreachable, ``plan()`` returns
None and the caller falls back to a plain single search.
"""

import json
import logging

import httpx

from librarian import config
from librarian.core.models import Plan

logger = logging.getLogger(__name__)

_VALID_FORMATS = {"epub", "pdf", "mobi", "azw3"}

_PROMPT = """Tu transformes une demande de livre(s) en un plan de recherche.
Réponds UNIQUEMENT en JSON avec ce schéma exact :
{{"queries": ["texte de recherche", ...], "language": "code ISO 639-1 ou \\"\\"", "format": "epub|pdf|mobi|azw3 ou \\"\\"", "title": "titre humain de l'ensemble", "series": true|false}}

Règles :
- Si la demande vise une série / intégrale / plusieurs tomes, mets UN texte de recherche par tome, avec le titre canonique réel de chaque tome, dans l'ordre, et series=true.
- Sinon un seul élément dans "queries" et series=false.
- N'invente pas de tomes : si la série t'est inconnue, mets la demande telle quelle en un seul élément.
- N'ajoute aucun texte hors du JSON.

Exemple —
Demande : "l'intégrale du Seigneur des anneaux en VF"
{{"queries": ["Le Seigneur des anneaux La Communauté de l'anneau", "Le Seigneur des anneaux Les Deux Tours", "Le Seigneur des anneaux Le Retour du roi"], "language": "fr", "format": "", "title": "Le Seigneur des anneaux", "series": true}}

Demande : "{request}"
"""


def enabled() -> bool:
    return bool(config.LLM_MODEL)


async def plan(request: str) -> Plan | None:
    """Return a Plan for ``request``, or None if the LLM is disabled/unavailable/unsure."""
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL}/api/generate",
                json={
                    "model": config.LLM_MODEL,
                    "prompt": _PROMPT.format(request=request.replace('"', "'")),
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "")
        return _to_plan(json.loads(raw))
    except Exception as e:
        logger.warning(f"LLM planner failed: {e}")
        return None


def _to_plan(data: dict) -> Plan | None:
    """Validate and normalise the model's JSON into a Plan (None if unusable)."""
    queries = data.get("queries")
    if not isinstance(queries, list):
        return None
    queries = [str(q).strip() for q in queries if str(q).strip()][:12]
    if not queries:
        return None
    fmt = str(data.get("format", "")).strip().lower()
    return Plan(
        queries=queries,
        language=str(data.get("language", "")).strip().lower()[:5],
        desired_format=fmt if fmt in _VALID_FORMATS else "",
        title=str(data.get("title", "")).strip()[:120],
        series=len(queries) > 1,  # a multi-query plan is a batch, whatever the model says
    )
