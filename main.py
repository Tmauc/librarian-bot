"""Entry point: wire the enabled client adapters to the core.

Only this file and ``librarian.clients.telegram`` know about Telegram. To add
another front-end (Discord, WhatsApp…), implement an adapter under
``librarian.clients`` and start it here — nothing in core changes.
"""

import asyncio
import glob
import logging
import os
import tempfile

import httpx
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from librarian import config
from librarian.clients.telegram.adapter import TelegramClient
from librarian.core import conversion, delivery, scanning
from librarian.sources import registry

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_notified_update: str | None = None


def _is_newer_version(remote: str, local: str) -> bool:
    """Return True if remote tag is strictly greater than local version."""
    def parse(v: str) -> tuple:
        try:
            return tuple(int(x) for x in v.lstrip("v").split("."))
        except ValueError:
            return (0,)
    return parse(remote) > parse(local)


async def check_for_updates(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _notified_update
    if not config.GITHUB_REPO:
        return
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "librarian-bot"}) as client:
            resp = await client.get(
                f"https://api.github.com/repos/{config.GITHUB_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 404:
                return
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        logger.warning(f"Update check failed: {e}")
        return

    tag = data.get("tag_name", "")
    if not tag or tag == _notified_update or not _is_newer_version(tag, config.VERSION):
        return
    _notified_update = tag
    url = data.get("html_url", f"https://github.com/{config.GITHUB_REPO}/releases/latest")
    msg = (
        f"🆕 Nouvelle version disponible : *{tag}*\n"
        f"Version installée : `{config.VERSION}`\n"
        f"[Voir les changements]({url})"
    )
    for uid in config.TELEGRAM_ALLOWED_IDS:
        try:
            await context.bot.send_message(uid, msg, parse_mode="Markdown", disable_web_page_preview=True)
        except Exception as e:
            logger.warning(f"Could not notify user {uid}: {e}")


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception in handler", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message is not None:
            await update.effective_message.reply_text(
                "😕 Une erreur inattendue est survenue. Réessaie dans un instant."
            )
    except Exception:
        pass


def _cleanup_orphaned_temp_files() -> None:
    count = 0
    for path in glob.glob(os.path.join(tempfile.gettempdir(), "librarian_*")):
        try:
            os.remove(path)
            count += 1
        except Exception:
            pass
    if count:
        logger.info(f"Cleaned up {count} orphaned temp file(s)")


def _banner() -> None:
    enabled = {s.name for s in registry.enabled_sources()}
    logger.info(f"--- librarian-bot v{config.VERSION} ---")
    for s in registry.all_sources():
        logger.info(f"  Source {s.name:<10}: {'✓ activée' if s.name in enabled else '✗ désactivée'}")
    logger.info(f"  Formats        : {', '.join(config.ALLOWED_FORMATS)}")
    logger.info(f"  VirusTotal     : {'✓ activé' if scanning.VT_API_KEY else '✗ désactivé'}")
    logger.info(f"  Calibre        : {'✓ trouvé' if conversion.ebook_convert_available() else '✗ absent (MOBI/AZW3 requièrent Calibre)'}")
    logger.info(f"  Email / Kindle : {'✓ activé' if delivery.is_configured() else '✗ désactivé'}")
    logger.info(f"  Mises à jour   : {'✓ ' + config.GITHUB_REPO if config.GITHUB_REPO else '✗ désactivées'}")
    logger.info(f"  Limite fichier : {config.TELEGRAM_MAX_FILE_SIZE // 1024 // 1024} MB{'  [local Bot API]' if config.LOCAL_API_SERVER else ''}")
    logger.info(f"  Utilisateurs   : {len(config.TELEGRAM_ALLOWED_IDS)} autorisé(s)")


def main() -> None:
    if not config.TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_TOKEN manquant — renseigne-le dans .env")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    client = TelegramClient()

    builder = Application.builder().token(config.TELEGRAM_TOKEN).concurrent_updates(True)
    if config.LOCAL_API_SERVER:
        builder = (
            builder
            .base_url(f"{config.LOCAL_API_SERVER}/bot")
            .base_file_url(f"{config.LOCAL_API_SERVER}/file/bot")
            .local_mode(True)
        )
        logger.info(f"Local Bot API mode: {config.LOCAL_API_SERVER}")
    app = builder.build()

    app.add_handler(CommandHandler("start", client.on_start))
    app.add_handler(CommandHandler("settings", client.on_settings))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, client.on_text))
    app.add_handler(CallbackQueryHandler(client.on_callback))
    app.add_error_handler(on_error)

    if config.GITHUB_REPO:
        app.job_queue.run_repeating(check_for_updates, interval=86400, first=30)

    _cleanup_orphaned_temp_files()
    if os.environ.get("ANNA_ARCHIVE_URL", "").startswith("http://"):
        logger.warning("ANNA_ARCHIVE_URL uses unencrypted HTTP — HTTPS is recommended")

    _banner()
    logger.info("Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
