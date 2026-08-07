"""Entry point: start every configured client adapter against the shared core.

Only the adapters under ``librarian/clients/<platform>`` know about a platform.
Adding another front-end means writing an adapter and starting it here — nothing
in the core changes.
"""

import asyncio
import contextlib
import glob
import logging
import os
import sys
import tempfile

import certifi

from librarian import config
from librarian.core import conversion, delivery, scanning
from librarian.sources import registry

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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


def _banner(clients: list[str]) -> None:
    enabled = {s.name for s in registry.enabled_sources()}
    logger.info(f"--- librarian-bot v{config.VERSION} ---")
    logger.info(f"  Clients        : {', '.join(clients)}")
    for s in registry.all_sources():
        logger.info(f"  Source {s.name:<10}: {'✓ activée' if s.name in enabled else '✗ désactivée'}")
    logger.info(f"  Formats        : {', '.join(config.ALLOWED_FORMATS)}")
    logger.info(f"  VirusTotal     : {'✓ activé' if scanning.VT_API_KEY else '✗ désactivé'}")
    logger.info(f"  Calibre        : {'✓ trouvé' if conversion.ebook_convert_available() else '✗ absent (MOBI/AZW3 requièrent Calibre)'}")
    logger.info(f"  Email / Kindle : {'✓ activé' if delivery.is_configured() else '✗ désactivé'}")
    logger.info(f"  Mises à jour   : {'✓ ' + config.GITHUB_REPO if config.GITHUB_REPO else '✗ désactivées'}")


async def _amain() -> None:
    _cleanup_orphaned_temp_files()
    if os.environ.get("ANNA_ARCHIVE_URL", "").startswith("http://"):
        logger.warning("ANNA_ARCHIVE_URL uses unencrypted HTTP — HTTPS is recommended")

    clients: list[str] = []
    telegram_app = None
    discord_client = None

    if config.TELEGRAM_TOKEN:
        from librarian.clients.telegram.adapter import create_application

        telegram_app = create_application()
        clients.append("telegram")

    if config.DISCORD_TOKEN:
        # discord.py (aiohttp) caches its default SSL context at import time; on the
        # macOS/Windows python.org builds that context often lacks CA certs and TLS
        # fails with "certificate verify failed". certifi is already installed (httpx
        # dep), so point OpenSSL at it BEFORE importing the adapter. Linux ships system
        # CA certs (aiohttp works natively), so its trust store is left untouched.
        # setdefault respects an explicit override.
        if sys.platform != "linux":
            os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        from librarian.clients.discord.adapter import DiscordClient

        discord_client = DiscordClient()
        clients.append("discord")

    if not clients:
        raise SystemExit("Aucun client configuré — renseigne TELEGRAM_TOKEN et/ou DISCORD_TOKEN dans .env")

    _banner(clients)

    background = []
    try:
        if telegram_app is not None:
            await telegram_app.initialize()
            await telegram_app.start()
            await telegram_app.updater.start_polling(drop_pending_updates=True)
        if discord_client is not None:
            background.append(asyncio.create_task(discord_client.start()))

        logger.info("Bot started.")
        if background:
            await asyncio.gather(*background)
        else:
            await asyncio.Event().wait()  # Telegram-only: run until interrupted
    finally:
        if telegram_app is not None:
            with contextlib.suppress(Exception):
                await telegram_app.updater.stop()
                await telegram_app.stop()
                await telegram_app.shutdown()
        if discord_client is not None:
            with contextlib.suppress(Exception):
                await discord_client.close()


def main() -> None:
    if not config.TELEGRAM_TOKEN and not config.DISCORD_TOKEN:
        raise SystemExit("Aucun client configuré — renseigne TELEGRAM_TOKEN et/ou DISCORD_TOKEN dans .env")
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(_amain())


if __name__ == "__main__":
    main()
