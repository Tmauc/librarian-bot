"""The entire conversation flow — platform-agnostic.

It talks only to a ``ClientContext`` and to core services. Adding a messaging
platform never touches this file; adding a download source never touches it either.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import tempfile
import time

from librarian import config
from librarian.clients.base import CANCEL, SKIP, Card, Choice, ClientContext
from librarian.core import (
    conversion,
    download_service,
    metadata,
    planner,
    prefs,
    scanning,
    search_service,
    series,
)
from librarian.core.metadata import BookMeta
from librarian.destinations import registry as destinations
from librarian.destinations.base import Destination

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"^[^@\s`]+@[^@\s`]+\.[^@\s`]+$")
_FMT_LABELS = {"epub": "📥 EPUB", "pdf": "📄 PDF", "mobi": "📱 MOBI", "azw3": "📘 AZW3"}


def _is_valid_email(value: str) -> bool:
    return len(value) <= 254 and bool(_EMAIL_RE.match(value))


def _fmt_size(size_bytes: int) -> str:
    if not size_bytes:
        return "?"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.0f} Ko"
    return f"{size_bytes / 1024 / 1024:.1f} Mo"


def _progress_bar(pct: int) -> str:
    filled = pct // 10
    return "▰" * filled + "▱" * (10 - filled)


def _cancel_btn() -> list[Choice]:
    return [Choice("⛔ Annuler", CANCEL)]


class _QuietContext:
    """A ctx proxy used during batch delivery: silences per-book chatter (``say`` /
    ``update_status``, including a destination's own status messages) while forwarding
    everything else — uploads, identity, limits, the session. This lets the batch loop
    own ONE consolidated live message instead of dozens of interleaved ones."""

    def __init__(self, ctx: ClientContext):
        self._ctx = ctx

    def __getattr__(self, name):  # forward uploads, user_key, data, max_file_size, session…
        return getattr(self._ctx, name)

    async def say(self, content) -> None:
        pass

    async def update_status(self, content, choices=None) -> None:
        pass


# ===========================================================================
# Entry points (called by an adapter)
# ===========================================================================
async def run_start(ctx: ClientContext) -> None:
    if not await prefs.get(ctx.user_key):
        await _onboard(ctx)
        return
    await ctx.say(
        "👋 Bonjour ! Envoie-moi le titre d'un livre et je le chercherai pour toi.\n\n"
        "Utilise /settings pour configurer tes préférences."
    )


async def run_settings(ctx: ClientContext) -> None:
    while True:
        p = await prefs.get(ctx.user_key)
        text = (
            "⚙️ Vos préférences :\n\n"
            f"• Format : {p.get('format', 'epub').upper()}\n"
            f"• Email : {p.get('email', 'non configuré')}\n"
            f"• Kindle : {p.get('kindle_email', 'non configuré')}"
        )
        choice = await ctx.ask_choice(
            text,
            [
                Choice("📚 Format", "fmt"),
                Choice("📧 Email", "email"),
                Choice("📖 Kindle", "kindle"),
                Choice("❌ Supprimer mes données", "delete"),
                Choice("✅ Fermer", "close"),
            ],
            cancellable=False,
        )
        if choice == "close":
            await ctx.say("👍 À bientôt ! Envoie un titre quand tu veux.")
            return
        if choice == "fmt":
            fmt = await ctx.ask_choice(
                "📚 Quel format préfères-tu ?",
                [Choice(_FMT_LABELS[f], f) for f in config.ALLOWED_FORMATS if f in _FMT_LABELS],
                cancellable=False,
            )
            await prefs.set(ctx.user_key, "format", fmt)
        elif choice == "email":
            addr = await _ask_optional_email(ctx, "📧 Envoie-moi ton adresse email :")
            if addr:
                await prefs.set(ctx.user_key, "email", addr)
        elif choice == "kindle":
            addr = await _ask_optional_email(
                ctx,
                "📖 Envoie-moi ton adresse Kindle :\n"
                "⚠️ Les vieux Kindle ne lisent pas l'EPUB — préfère MOBI/AZW3.",
            )
            if addr:
                await prefs.set(ctx.user_key, "kindle_email", addr)
        elif choice == "delete":
            confirm = await ctx.ask_choice(
                "⚠️ Ceci supprimera toutes tes préférences. Continuer ?",
                [Choice("✅ Oui, supprimer", "yes"), Choice("❌ Non", "no")],
                cancellable=False,
            )
            if confirm == "yes":
                await prefs.delete_user(ctx.user_key)
                await ctx.say("✅ Préférences supprimées. Utilise /settings pour reconfigurer.")
                return


async def run_search(ctx: ClientContext, query: str) -> None:
    now = time.monotonic()
    if now - ctx.data.get("last_search_at", 0.0) < config.RATE_LIMIT_SECONDS:
        await ctx.say(f"⏳ Attends {config.RATE_LIMIT_SECONDS} secondes entre deux recherches.")
        return
    ctx.data["last_search_at"] = now

    query = query.strip()
    if not query:
        return
    if len(query) > config.MAX_QUERY_LENGTH:
        await ctx.say(f"❌ Requête trop longue (max {config.MAX_QUERY_LENGTH} caractères).")
        return

    # Smart multi-book intent (« l'intégrale de X ») → LLM plan → batch, when enabled.
    if planner.enabled() and _looks_like_batch(query):
        p = await planner.plan(query)
        logger.info(f"LLM plan for {query!r}: {p}")
        if p and p.series:
            await _run_batch(ctx, p)
            return

    await ctx.say("🔍 Recherche en cours…")
    results = await search_service.search(query, ctx.max_file_size)
    if not results:
        await ctx.say(f"😕 Aucun résultat trouvé pour « {query} ».\nEssaie un autre titre ou orthographe.")
        return

    has_epub = any(r.ext == "epub" for r in results)
    non_epub = [r for r in results if r.ext != "epub"]
    if not has_epub and non_epub:
        exts = ", ".join(sorted({r.ext for r in non_epub})).upper()
        ok = await ctx.ask_choice(
            f"📚 Pas d'epub pour « {query} ». {len(results)} résultat(s) en {exts}. Ça ira ?",
            [Choice(f"✅ Oui, en {exts}", "yes"), Choice("❌ Non", "no")],
        )
        if ok == "no":
            await ctx.say("🔍 Recherche annulée. Envoie un nouveau titre quand tu veux !")
            return

    idx = await _choose_result(ctx, results, query, has_epub)
    result = results[idx]

    # Format: only EPUB sources can be converted; others are delivered as-is.
    src_ext = result.ext or "epub"
    offer = list(config.ALLOWED_FORMATS) if src_ext == "epub" else [src_ext]
    if len(offer) > 1:
        desired_fmt = await ctx.ask_choice(
            f"📚 « {result.title[:60]} »\nQuel format veux-tu ?",
            [Choice(_FMT_LABELS[f], f) for f in offer if f in _FMT_LABELS],
        )
    else:
        desired_fmt = offer[0] if offer else "epub"

    destination = await _pick_destination(ctx)
    await _deliver(ctx, results, idx, desired_fmt, destination)


async def _pick_destination(ctx: ClientContext):
    """Ask where to send the file, or auto-pick when only one destination is available."""
    available = await destinations.available_for(ctx)
    if len(available) > 1:
        chosen = await ctx.ask_choice(
            "📬 Où envoyer ?", [Choice(d.label, d.name) for d in available]
        )
        return destinations.get(chosen)
    return available[0]  # "here" is always available


# ===========================================================================
# Smart multi-book (batch)
# ===========================================================================
_BATCH_HINTS = (
    "intégrale", "integrale", "tous les tomes", "toute la série", "toute la serie",
    "série complète", "serie complete", "collection complète", "collection complete",
    "saga", "trilogie", "les tomes", "tomes 1", "premiers tomes", "l'ensemble des",
    "complete series", "all volumes", "whole series",
)


def _looks_like_batch(query: str) -> bool:
    ql = query.lower()
    return any(h in ql for h in _BATCH_HINTS)


_LANG_NAMES = {
    "fr": ("fr", "franç", "francais", "french"),
    "en": ("en", "english", "anglais"),
    "es": ("es", "español", "espagnol", "spanish"),
    "de": ("de", "deutsch", "allemand", "german"),
    "it": ("it", "italien", "italiano", "italian"),
}


def _lang_match(result_language: str, code: str) -> bool:
    rl = (result_language or "").lower()
    return any(rl.startswith(p) for p in _LANG_NAMES.get(code, (code,)))


def _detect_tome(title: str) -> int | None:
    """Best-effort volume number from a catalogue title (tome N / TNN / (… N))."""
    t = title.lower()
    for pat in (r"\btome\s*0*(\d{1,2})\b", r"\bt0*(\d{1,2})\b", r"\(.*?\b0*(\d{1,2})\s*\)", r"[-–:]\s*0*(\d{1,2})\s*[-–:]"):
        m = re.search(pat, t)
        if m:
            return int(m.group(1))
    return None


def _vol_label(num: int | None, title: str) -> str:
    return f"Tome {num} — {title}"[:100] if num is not None else title[:100]


async def _run_batch(ctx: ClientContext, plan) -> None:
    """Identify the series (Wikidata → canonical ordered volumes), find each volume's file
    in the catalogue (in the requested language), and let the user multi-select the tomes.
    Falls back to raw catalogue results if the series is unknown to Wikidata."""
    await ctx.say(f"🔎 Identification de « {plan.query} »…")
    vols = await series.volumes(plan.query, plan.language or "fr")
    entries = await _series_entries(ctx, plan, vols) if vols else []

    if entries:  # clean, ordered "Tome N — title" → several candidate editions
        choices = [
            Choice(_vol_label(n, t), str(i), description=_meta_line(cands[0]))
            for i, (n, t, cands) in enumerate(entries)
        ]
        card = Card(
            title=f"🧠 {plan.query}",
            description=f"{len(entries)} tome(s) trouvé(s) — coche ceux à télécharger :",
            footer="Série identifiée via Wikidata + catalogue",
        )
        picked = await ctx.ask_multi_choice(card, choices)
        # Each pick keeps its candidate editions (download can fall back) + a metadata
        # hint (canonical series/number/title from Wikidata) for clean tagging.
        chosen = []
        for v in picked:
            num, title, cands = entries[int(v)]
            hint = BookMeta(title=title, series=plan.query, index=num, language=plan.language)
            chosen.append((_vol_label(num, title), cands, hint))
    else:  # fallback: unknown series → raw catalogue, user sorts it out
        await ctx.say(f"🔎 Recherche de « {plan.query} »…")
        results = await search_service.search(plan.query, ctx.max_file_size)
        if not results:
            await ctx.say(f"😕 Rien trouvé pour « {plan.query} ».")
            return
        card = Card(title=f"🧠 {plan.query}", description="Coche les tomes à télécharger :", footer=f"{len(results)} résultat(s)")
        picked = await ctx.ask_multi_choice(card, [_result_choice(i, results[i]) for i in range(len(results))])
        chosen = [
            (results[int(v)].title or "livre", [results[int(v)]], BookMeta(language=plan.language))
            for v in picked
        ]

    if not chosen:
        await ctx.say("🔍 Aucun tome sélectionné. À bientôt !")
        return

    destination = await _pick_destination(ctx)
    desired_fmt = plan.desired_format if plan.desired_format in config.ALLOWED_FORMATS else config.ALLOWED_FORMATS[0]
    await _run_batch_downloads(ctx, plan.query, chosen, desired_fmt, destination)


async def _run_batch_downloads(ctx: ClientContext, series_name: str, chosen, desired_fmt, destination) -> None:
    """Download every chosen tome under ONE live message: a global progress bar + a
    per-tome checklist that fills in as each finishes. Each tome carries several
    candidate editions so a dead mirror falls back instead of failing the tome."""
    total = len(chosen)
    log: list[tuple[bool, str]] = []  # (delivered?, label) in order
    quiet = _QuietContext(ctx)
    for label, candidates, hint in chosen:
        await ctx.update_status(_batch_card(series_name, log, current=label, total=total), _cancel_btn())
        ok = await _deliver(quiet, candidates, 0, desired_fmt, destination, meta_hint=hint)
        log.append((ok, label))
    # Final frame of the live message (no "in progress" line, no cancel button)…
    await ctx.update_status(_batch_card(series_name, log, current=None, total=total))
    delivered = sum(1 for ok, _ in log if ok)
    tail = "" if delivered == total else "\nRéessaie les tomes ❌ dans quelques minutes — mirrors momentanément indispo."
    await ctx.say(f"✅ Terminé — {delivered}/{total} tome(s) livré(s).{tail}")


def _batch_card(series_name: str, log: list[tuple[bool, str]], current: str | None, total: int) -> Card:
    """The single evolving batch message: global bar + ✅/❌ checklist + current tome."""
    done = len(log)
    pct = int(done / total * 100) if total else 0
    lines = [f"{'✅' if ok else '❌'} {label[:70]}" for ok, label in log]
    if current is not None:
        lines.append(f"⏳ {current[:70]}…")
    return Card(
        title=f"🧠 {series_name}",
        description=f"{_progress_bar(pct)}  {done}/{total} tome(s)\n\n" + "\n".join(lines),
        footer="Téléchargement de la série…" if current is not None else "Série téléchargée",
    )


async def _series_entries(ctx: ClientContext, plan, vols: list[tuple]):
    """Map each canonical volume to its best catalogue candidates (several editions,
    preferring the requested language), then backfill any numbered tome the catalogue
    has but Wikidata missed. Returns ordered ``(number, title, [results])`` tuples."""
    lang = plan.language
    searches = await asyncio.gather(
        *[search_service.search(f"{plan.query} {title}", ctx.max_file_size) for _, title in vols]
    )
    entries: list[tuple] = []
    covered: set[int] = set()
    for (num, title), results in zip(vols, searches, strict=True):
        cands = _best_matches(title, results, lang)
        if cands:
            entries.append((num, title, cands))
            if num is not None:
                covered.add(num)

    # Backfill: numbered tomes present in the catalogue but missing from Wikidata (data gaps).
    for r in await search_service.search(plan.query, ctx.max_file_size):
        if lang and r.language and not _lang_match(r.language, lang):
            continue
        num = _detect_tome(r.title)
        if num is not None and num not in covered:
            covered.add(num)
            entries.append((num, r.title, [r]))

    entries.sort(key=lambda e: (e[0] is None, e[0] if e[0] is not None else 0))
    return entries


def _best_matches(vol: str, results, language: str = "", limit: int = 5):
    """Up to ``limit`` results sharing a distinctive word with the volume title, the
    requested language first — several candidate editions so download can fall back
    when a mirror is dead. Empty if nothing plausible."""
    words = {w for w in re.sub(r"[^\w]", " ", vol.lower()).split() if len(w) > 3}
    matches = [r for r in results[:12] if not words or any(w in r.title.lower() for w in words)]
    if language:
        preferred = [r for r in matches if r.language and _lang_match(r.language, language)]
        rest = [r for r in matches if r not in preferred]
        matches = preferred + rest
    return matches[:limit]


# ===========================================================================
# Result list + detail card
# ===========================================================================
async def _choose_result(ctx: ClientContext, results, query: str, has_epub: bool) -> int:
    """Show the enriched list; open a detail card on selection; return the chosen
    index once the user hits Télécharger (loops back to the list on Retour)."""
    shown = [i for i, r in enumerate(results) if not (r.ext != "epub" and has_epub)]
    while True:
        pick = await ctx.ask_choice(
            _results_card(query, results, shown), [_result_choice(i, results[i]) for i in shown]
        )
        idx = int(pick)
        action = await ctx.ask_choice(
            await _detail_card(results[idx]),
            [Choice("⬇️ Télécharger", "dl"), Choice("⬅️ Retour à la liste", "back")],
        )
        if action == "dl":
            return idx


def _meta_line(r) -> str:
    bits = [r.author, r.year, r.language, (_fmt_size(r.size_bytes) if r.size_bytes else ""), (r.ext or "").upper()]
    return " · ".join(b for b in bits if b)


def _result_choice(i: int, r) -> Choice:
    return Choice(label=f"{i + 1}. {(r.title or '?')[:80]}", value=str(i), description=_meta_line(r)[:100])


def _results_card(query: str, results, shown: list[int]) -> Card:
    lines = []
    for n, i in enumerate(shown, 1):
        r = results[i]
        icon = "📥" if not r.is_torrent else "🌀"
        lines.append(f"{n}. {icon} {r.title}\n     {_meta_line(r)}")
    return Card(
        title=f"📚 {len(shown)} résultat(s) pour « {query[:60]} »",
        description="\n\n".join(lines)[:3900],
        footer="Choisis un livre pour voir sa fiche",
    )


async def _detail_card(r) -> Card:
    extra = await download_service.details(r)
    description = extra.get("description") or r.description or "(pas de description disponible)"
    cover = extra.get("cover") or r.cover or None
    fields = []
    if r.author:
        fields.append(("Auteur", r.author))
    if r.year:
        fields.append(("Année", r.year))
    if r.language:
        fields.append(("Langue", r.language))
    fields.append(("Format", (r.ext or "?").upper()))
    if r.size_bytes:
        fields.append(("Taille", _fmt_size(r.size_bytes)))
    return Card(
        title=(r.title or "?")[:250], description=description[:1000],
        fields=fields, thumbnail=cover, footer="⬇️ Télécharger  ·  ⬅️ Retour",
    )


# ===========================================================================
# Internals
# ===========================================================================
async def _onboard(ctx: ClientContext) -> None:
    fmt = await ctx.ask_choice(
        "👋 Bienvenue ! Commençons par tes préférences.\n📚 Quel format préfères-tu ?",
        [Choice(_FMT_LABELS[f], f) for f in config.ALLOWED_FORMATS if f in _FMT_LABELS],
        cancellable=False,
    )
    await prefs.set(ctx.user_key, "format", fmt)

    email = await _ask_optional_email(ctx, "📧 Envoie ton adresse email pour recevoir les livres (ou Passer).")
    if email:
        await prefs.set(ctx.user_key, "email", email)

    kindle = await _ask_optional_email(
        ctx,
        "📖 Envoie ton adresse Kindle (ou Passer).\n⚠️ Vieux Kindle : préfère MOBI/AZW3.",
    )
    if kindle:
        await prefs.set(ctx.user_key, "kindle_email", kindle)

    p = await prefs.get(ctx.user_key)
    await ctx.say(
        "✅ Configuration terminée !\n\n"
        f"• Format : {p.get('format', '?').upper()}\n"
        f"• Email : {p.get('email', 'non configuré')}\n"
        f"• Kindle : {p.get('kindle_email', 'non configuré')}\n\n"
        "Envoie un titre de livre pour commencer. /settings pour modifier."
    )


async def _ask_optional_email(ctx: ClientContext, prompt: str) -> str | None:
    while True:
        res = await ctx.ask_text(prompt, buttons=[Choice("⏭️ Passer", SKIP)])
        if res == SKIP:
            return None
        if _is_valid_email(res):
            return res
        await ctx.say("❌ Adresse invalide. Réessaie, ou clique Passer.")


async def _deliver(
    ctx: ClientContext, results, start_idx: int, desired_fmt: str, destination: Destination,
    meta_hint: BookMeta | None = None,
) -> bool:
    """Fetch → convert → tag metadata → scan → deliver one book. Returns True if delivered.

    ``meta_hint`` carries what the caller already knows (series name + volume number +
    language in batch mode) so the file lands with clean, correctly-grouped metadata.

    In batch mode the caller wraps ``ctx`` in a ``_QuietContext`` so all the
    per-book chatter is silenced and only the bool result drives the global view.
    """
    file_path = None
    converted_path = None
    try:
        outcome = await _fetch_with_retry(ctx, results, start_idx, desired_fmt)
        if outcome is None:
            await ctx.say("😕 Aucun résultat disponible dans la limite de taille.\nRefais une recherche.")
            return False
        if outcome == "mirrors":
            await ctx.say(
                "😕 Toutes les sources de téléchargement sont indisponibles pour l'instant.\n"
                "Réessaie dans quelques minutes ou essaie un autre titre."
            )
            return False

        file_path, result = outcome
        title = result.title or "livre"
        ext = result.ext or "epub"
        send_path, send_ext = file_path, ext

        if ext == "epub" and desired_fmt != "epub":
            try:
                await ctx.say(f"🔄 Conversion en {desired_fmt.upper()} de « {title[:50]} »…")
                if desired_fmt == "pdf":
                    converted_path = await conversion.epub_to_pdf(file_path)
                elif desired_fmt == "mobi":
                    converted_path = await conversion.epub_to_mobi(file_path)
                elif desired_fmt == "azw3":
                    converted_path = await conversion.epub_to_azw3(file_path)
                if converted_path:
                    send_path, send_ext = converted_path, desired_fmt
            except Exception as e:
                logger.warning(f"Conversion to {desired_fmt} failed: {e}")
                await ctx.say(f"⚠️ Conversion {desired_fmt.upper()} échouée, envoi en EPUB à la place.")
                send_path, send_ext = file_path, ext

        # Rewrite metadata (title/author/series+number/language/cover) before scan+send.
        # Best-effort: a tagging failure never blocks delivery.
        if send_ext in ("epub", "mobi", "azw3"):
            try:
                if await metadata.tag(send_path, send_ext, result, meta_hint):
                    logger.info(f"Metadata tagged: {title[:60]!r}")
            except Exception as e:
                logger.warning(f"Metadata tagging failed for {title[:60]!r}: {e}")

        vt_caption = await _scan(ctx, send_path, title)
        if vt_caption is None:  # blocked as malicious
            return False

        safe_title = re.sub(r"[^\w\s\-]", "", title).strip()[:60] or "livre"
        filename = f"{safe_title}.{send_ext}"

        await destination.deliver(ctx, send_path, filename, title, vt_caption)
        return True
    finally:
        for p in (file_path, converted_path):
            if p and p.startswith(tempfile.gettempdir()):
                with contextlib.suppress(Exception):
                    os.remove(p)


async def _fetch_with_retry(ctx: ClientContext, results, start_idx: int, desired_fmt: str):
    """Try results[start_idx:], auto-retrying past failures/oversized files.

    Returns (path, result), None (nothing fit), or 'mirrors' (only mirror failures).
    """
    any_mirror_failure = False
    for i in range(start_idx, len(results)):
        result = results[i]
        ext = result.ext or "epub"

        # Only an EPUB converts to MOBI/AZW3; PDF target accepts EPUB or PDF.
        if desired_fmt in ("mobi", "azw3") and ext != "epub":
            continue
        if desired_fmt == "pdf" and ext not in ("epub", "pdf"):
            continue

        if i > start_idx:
            await ctx.update_status(f"🔄 Essai du résultat suivant : « {result.title or 'livre'} »…", _cancel_btn())

        try:
            path = await _download_one(ctx, result)
        except TimeoutError:
            logger.warning(f"Timeout on result {i}, skipping")
            any_mirror_failure = True
            continue
        except Exception as e:  # CancelledError is a BaseException — it propagates
            logger.warning(f"Result {i} failed ({e}), skipping")
            any_mirror_failure = True
            continue

        if os.path.getsize(path) > ctx.max_file_size:
            logger.info(f"Result {i} too large, skipping")
            with contextlib.suppress(Exception):
                os.remove(path)
            continue

        return path, result

    return "mirrors" if any_mirror_failure else None


async def _download_one(ctx: ClientContext, result) -> str:
    """Download a single result, driving the preparing/progress UI. Raises on failure."""
    t = result.title or "livre"
    is_torrent = result.is_torrent
    prep = asyncio.Event()

    async def on_progress(d: int, total: int) -> None:
        prep.set()
        if total:
            pct = min(int(d / total * 100), 99)
            await ctx.update_status(
                f"⬇️ « {t} »\n{_progress_bar(pct)} {pct}%  ({_fmt_size(d)} / {_fmt_size(total)})", _cancel_btn()
            )
        else:
            await ctx.update_status(f"⬇️ « {t} »\n{_fmt_size(d)} téléchargés…", _cancel_btn())

    async def animate() -> None:
        frames = ["⏳ Recherche du fichier .", "⏳ Recherche du fichier ..", "⏳ Recherche du fichier ..."]
        k = 0
        while not prep.is_set():
            await ctx.update_status(frames[k % 3], _cancel_btn())
            k += 1
            await asyncio.sleep(1)

    if is_torrent:
        await ctx.update_status(
            f"🌀 Envoi vers le client torrent pour « {t} »…\n⏳ Surveillance du dossier…", _cancel_btn()
        )
        anim_task = None
    else:
        await ctx.update_status("⏳ Préparation…", _cancel_btn())
        anim_task = asyncio.create_task(animate())

    try:
        return await download_service.fetch(
            result, on_progress=None if is_torrent else on_progress, max_bytes=ctx.max_file_size
        )
    finally:
        if anim_task:
            anim_task.cancel()


async def _scan(ctx: ClientContext, path: str, title: str) -> str | None:
    """Return a caption suffix (possibly empty), or None if the file is blocked."""
    if not scanning.VT_API_KEY:
        return ""
    stop = asyncio.Event()

    async def animate() -> None:
        k = 0
        while not stop.is_set():
            await ctx.update_status(f"🔍 Analyse antivirus de « {title[:45]} » " + "." * (k % 3 + 1))
            k += 1
            await asyncio.sleep(1)

    anim = asyncio.create_task(animate())
    try:
        stats = await scanning.scan_file(path)
    except Exception as e:
        logger.warning(f"VirusTotal scan failed: {e}")
        return "\n⚠️ Analyse VirusTotal indisponible"
    finally:
        stop.set()
        anim.cancel()

    if not stats:
        return ""
    malicious = stats.get("malicious", 0)
    suspicious = stats.get("suspicious", 0)
    if malicious > 0:
        await ctx.say(f"🚨 Fichier bloqué — détecté comme malveillant par {malicious} scanner(s) VirusTotal.")
        return None
    if suspicious > 0:
        return f"\n⚠️ Signalé comme suspect par {suspicious} scanner(s) VirusTotal"
    return ""
