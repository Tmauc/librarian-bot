"""The Destination contract.

A destination is where a downloaded (and converted/scanned) book file is finally
sent — this chat, an email, a Kindle… Destinations are pluggable behind this
contract and registered once (see registry.py); adding one touches no core, flow,
source or client code.

A destination owns its own user-facing messages (via ``ctx.say``) and its own error
handling, so the flow just picks one and calls ``deliver``.
"""

from __future__ import annotations

import abc
import logging
import re
from typing import TYPE_CHECKING

from librarian.core import delivery, prefs

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext
    from librarian.core.metadata import BookMeta

logger = logging.getLogger(__name__)

# --- Cloud folder organisation ---------------------------------------------
# How cloud destinations file uploaded books into sub-folders. Stored per user as
# the ``sort_scheme`` pref; the path is built from the book's (clean) metadata.
SORT_SCHEMES: dict[str, str] = {
    "author_series": "📂 Auteur / Série",
    "author": "📂 Par auteur",
    "series": "📂 Par série",
    "flat": "📄 Racine (aucun dossier)",
}
DEFAULT_SORT_SCHEME = "author_series"


def _safe_segment(name: str) -> str:
    """A filesystem/cloud-safe folder name (drop path separators & control chars)."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name or "").strip().strip(".")
    return re.sub(r"\s+", " ", cleaned)[:80]


def subfolders(scheme: str, meta: BookMeta) -> list[str]:
    """The ordered sub-folder segments for a book under the destination root."""
    author = _safe_segment(getattr(meta, "author", "")) or "Sans auteur"
    series = _safe_segment(getattr(meta, "series", ""))
    if scheme == "author":
        return [author]
    if scheme == "series":
        return [series] if series else []
    if scheme == "author_series":
        return [author, series] if series else [author]
    return []  # flat


async def sort_scheme_for(ctx: ClientContext) -> str:
    scheme = (await prefs.get(ctx.user_key)).get("sort_scheme", DEFAULT_SORT_SCHEME)
    return scheme if scheme in SORT_SCHEMES else DEFAULT_SORT_SCHEME


class Destination(abc.ABC):
    #: unique registry name (also used as the choice value in the menu).
    name: str = "destination"
    #: button label shown to the user.
    label: str = "Destination"

    async def available(self, ctx: ClientContext) -> bool:
        """Whether this destination can be offered to this user right now
        (e.g. an email destination needs SMTP configured and an address on file)."""
        return True

    @abc.abstractmethod
    async def deliver(
        self, ctx: ClientContext, path: str, filename: str, title: str, caption: str,
        meta: BookMeta | None = None,
    ) -> None:
        """Send ``path`` to the user. ``caption`` is a short suffix (e.g. a VirusTotal
        warning) to append where relevant. ``meta`` carries the book's clean metadata
        (author/series/number) so folder-organising destinations can file it; plain
        destinations ignore it. Handle expected errors and message the user."""


class MailDestination(Destination):
    """Reusable base for SMTP-backed destinations (email, Send to Kindle)."""

    pref_key: str = "email"      # which stored address to use
    kindle: bool = False         # sets the "convert" subject for Send to Kindle
    channel: str = "email"       # human word used in status messages

    async def available(self, ctx: ClientContext) -> bool:
        return delivery.is_configured() and bool((await prefs.get(ctx.user_key)).get(self.pref_key))

    async def deliver(
        self, ctx: ClientContext, path: str, filename: str, title: str, caption: str,
        meta: BookMeta | None = None,
    ) -> None:
        addr = (await prefs.get(ctx.user_key)).get(self.pref_key)
        if not addr:
            await ctx.say(f"❌ Adresse {self.channel} non configurée. Utilise /settings")
            return
        try:
            await ctx.say(f"📤 Envoi par {self.channel} à {addr}…")
            await delivery.send_file(path, filename, addr, kindle=self.kindle)
            await ctx.say(f"✅ Envoyé à {addr} ✅")
        except Exception as e:
            logger.warning(f"{self.name} send failed: {e}")
            await ctx.say(f"❌ Envoi {self.channel} échoué. Vérifie l'adresse et la config SMTP dans /settings.")


class CloudUploadDestination(Destination):
    """Reusable base for 'upload the file to a cloud folder' destinations.

    Subclasses implement ``available`` (credentials present) and ``_upload`` (the
    provider-specific token refresh + upload). This base owns the status messages and
    error handling. A cloud folder feeds e-readers that sync it (e.g. a Kobo pointed
    at the same Dropbox / Google Drive)."""

    where: str = "le cloud"  # human label used in status messages

    async def deliver(
        self, ctx: ClientContext, path: str, filename: str, title: str, caption: str,
        meta: BookMeta | None = None,
    ) -> None:
        folders = subfolders(await sort_scheme_for(ctx), meta) if meta is not None else []
        try:
            await ctx.say(f"☁️ Envoi vers {self.where}…")
            await self._upload(path, filename, folders)
            where = f"{self.where}/{'/'.join(folders)}" if folders else self.where
            await ctx.say(f"✅ Déposé sur {where} — synchronise ta liseuse 📖")
        except Exception as e:
            logger.warning(f"{self.name} upload failed: {e}")
            await ctx.say(f"❌ Envoi vers {self.where} échoué. Vérifie la configuration.")

    @abc.abstractmethod
    async def _upload(self, path: str, filename: str, subfolders: list[str]) -> None:
        """Upload the file to the provider under ``subfolders`` (created as needed).
        ``subfolders`` empty = the destination root. Raise on failure."""
