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

_PROMPT = """Tu extrais l'intention d'une demande de livre(s) pour lancer une recherche.
Réponds UNIQUEMENT en JSON avec ce schéma exact :
{{"query": "nom du livre ou de la série", "language": "code ISO 639-1 ou \\"\\"", "format": "epub|pdf|mobi|azw3 ou \\"\\"", "series": true|false}}

Règles :
- "query" = UNIQUEMENT le titre du livre ou le nom de la série, tel qu'il figurerait sur la couverture.
- ENLÈVE tout le reste : les intentions ("je veux", "trouve-moi"), les mots "l'intégrale de / tous les tomes / saga / coffret", la langue ("en VF", "en anglais"), le FORMAT, et surtout le NOM DE L'AUTEUR.
- N'INVENTE PAS de titres de tomes ; un seul champ "query".
- series=true si la demande vise une série / intégrale / plusieurs tomes ; sinon false.
- language : "en VF"/"français" → "fr" ; "en anglais"/"in English" → "en" ; sinon "".
- Aucun texte hors du JSON.

Exemples —
Demande : "Je veux l'intégrale des chevaliers d'émeraude en VF"
{{"query": "Les Chevaliers d'Émeraude", "language": "fr", "format": "", "series": true}}
Demande : "je veux l'intégrale de Dune de Frank Herbert"
{{"query": "Dune", "language": "", "format": "", "series": true}}
Demande : "dune de frank herbert en epub"
{{"query": "Dune", "language": "", "format": "epub", "series": false}}
Demande : "l'intégrale épée de la vérité en vf"
{{"query": "L'Épée de vérité", "language": "fr", "format": "", "series": true}}

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
    query = str(data.get("query", "")).strip()
    if not query:
        return None
    fmt = str(data.get("format", "")).strip().lower()
    return Plan(
        query=query[:200],
        language=str(data.get("language", "")).strip().lower()[:5],
        desired_format=fmt if fmt in _VALID_FORMATS else "",
        series=bool(data.get("series")),
    )
