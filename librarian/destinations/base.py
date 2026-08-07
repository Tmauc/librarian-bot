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
from typing import TYPE_CHECKING

from librarian.core import delivery, prefs

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext

logger = logging.getLogger(__name__)


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
    async def deliver(self, ctx: ClientContext, path: str, filename: str, title: str, caption: str) -> None:
        """Send ``path`` to the user. ``caption`` is a short suffix (e.g. a VirusTotal
        warning) to append where relevant. Handle expected errors and message the user."""


class MailDestination(Destination):
    """Reusable base for SMTP-backed destinations (email, Send to Kindle)."""

    pref_key: str = "email"      # which stored address to use
    kindle: bool = False         # sets the "convert" subject for Send to Kindle
    channel: str = "email"       # human word used in status messages

    async def available(self, ctx: ClientContext) -> bool:
        return delivery.is_configured() and bool((await prefs.get(ctx.user_key)).get(self.pref_key))

    async def deliver(self, ctx: ClientContext, path: str, filename: str, title: str, caption: str) -> None:
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
