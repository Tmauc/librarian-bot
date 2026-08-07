"""Deliver into the current chat via the active client (Telegram, Discord…)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from librarian.destinations.base import Destination

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext


class ThisChatDestination(Destination):
    name = "here"
    label = "📬 Ici (ce chat)"

    async def available(self, ctx: ClientContext) -> bool:
        return True  # always possible — the user is talking to us right now

    async def deliver(self, ctx: ClientContext, path: str, filename: str, title: str, caption: str) -> None:
        await ctx.say(f"📤 Envoi de « {title} »…")
        await ctx.send_document(path, filename, f"📖 {title}{caption}")
        await ctx.say("✅ Envoyé ! Bonne lecture 📖")
