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
from librarian.clients.base import CANCEL, SKIP, Choice, ClientContext
from librarian.core import conversion, delivery, download_service, prefs, scanning, search_service

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

    choices = []
    for i, r in enumerate(results):
        if r.ext != "epub" and has_epub:
            continue  # hide non-epub when epub exists (indices preserved)
        icon = "📥" if not r.is_torrent else "🌀"
        title = r.title or "?"
        short = title[:45] + "…" if len(title) > 45 else title
        choices.append(Choice(f"{icon} {short}", str(i)))

    pick = await ctx.ask_choice(f"📚 {len(choices)} résultat(s) :", choices)
    idx = int(pick)
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

    # Destination: this chat, or a configured email/Kindle address.
    p = await prefs.get(ctx.user_key)
    dest_choices = [Choice("📬 Ici (ce chat)", "here")]
    if p.get("email"):
        dest_choices.append(Choice("📧 Email", "email"))
    if p.get("kindle_email"):
        dest_choices.append(Choice("📖 Kindle", "kindle"))
    if len(dest_choices) > 1:
        destination = await ctx.ask_choice(f"📚 « {result.title[:50]} »\n\n📬 Où envoyer ?", dest_choices)
    else:
        destination = "here"

    await _deliver(ctx, results, idx, desired_fmt, destination)


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


async def _deliver(ctx: ClientContext, results, start_idx: int, desired_fmt: str, destination: str) -> None:
    file_path = None
    converted_path = None
    try:
        outcome = await _fetch_with_retry(ctx, results, start_idx, desired_fmt)
        if outcome is None:
            await ctx.say("😕 Aucun résultat disponible dans la limite de taille.\nRefais une recherche.")
            return
        if outcome == "mirrors":
            await ctx.say(
                "😕 Toutes les sources de téléchargement sont indisponibles pour l'instant.\n"
                "Réessaie dans quelques minutes ou essaie un autre titre."
            )
            return

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

        vt_caption = await _scan(ctx, send_path, title)
        if vt_caption is None:  # blocked as malicious
            return

        safe_title = re.sub(r"[^\w\s\-]", "", title).strip()[:60] or "livre"
        filename = f"{safe_title}.{send_ext}"

        if destination == "here":
            await ctx.say(f"📤 Envoi de « {title} »…")
            await ctx.send_document(send_path, filename, f"📖 {title}{vt_caption}")
            await ctx.say("✅ Envoyé ! Bonne lecture 📖")
        elif destination in ("email", "kindle"):
            key = "email" if destination == "email" else "kindle_email"
            addr = (await prefs.get(ctx.user_key)).get(key)
            if not addr:
                await ctx.say(f"❌ Adresse {destination} non configurée. Utilise /settings")
                return
            label = "Kindle" if destination == "kindle" else "email"
            try:
                await ctx.say(f"📧 Envoi par {label} à {addr}…")
                await delivery.send_file(send_path, filename, addr, kindle=(destination == "kindle"))
                await ctx.say(f"✅ Envoyé à {addr} ✅")
            except Exception as e:
                logger.warning(f"{destination} send failed: {e}")
                await ctx.say(f"❌ Envoi {label} échoué. Vérifie l'adresse et la config SMTP dans /settings.")
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
