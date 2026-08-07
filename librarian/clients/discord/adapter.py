"""Discord adapter — the ONLY Discord-specific code.

Mirror of the Telegram adapter: it renders the generic port (ClientContext) onto
discord.py primitives (messages + button Views), routes incoming events into the
per-user Session, and enforces the Discord whitelist and file-size limit. The
conversation itself lives entirely in ``librarian.clients.flow``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord

from librarian import config
from librarian.clients import flow
from librarian.clients.base import CANCEL, Choice, ClientContext, Session

logger = logging.getLogger(__name__)


class _FlowView(discord.ui.View):
    """A button row whose clicks are routed back to the client by custom_id."""

    def __init__(self, client: DiscordClient, choices: list[Choice]):
        super().__init__(timeout=None)
        for i, c in enumerate(choices):
            style = discord.ButtonStyle.danger if c.value == CANCEL else discord.ButtonStyle.secondary
            button = discord.ui.Button(label=c.label[:80], custom_id=c.value, row=min(i // 5, 4), style=style)
            button.callback = self._make_callback(client, c.value)
            self.add_item(button)

    @staticmethod
    def _make_callback(client: DiscordClient, value: str):
        async def _cb(interaction: discord.Interaction) -> None:
            await client._on_button(interaction, value)

        return _cb


class DiscordContext(ClientContext):
    """Renders the generic port onto a single Discord channel."""

    def __init__(self, session: Session, client: DiscordClient, channel):
        super().__init__(session)
        self._client = client
        self._channel = channel

    @property
    def max_file_size(self) -> int:
        return config.DISCORD_MAX_FILE_SIZE

    def _view(self, choices: list[Choice] | None):
        return _FlowView(self._client, choices) if choices else None

    async def _send(self, text: str, choices: list[Choice] | None = None):
        return await self._channel.send(text, view=self._view(choices))

    async def _edit(self, handle, text: str, choices: list[Choice] | None = None) -> None:
        await handle.edit(content=text, view=self._view(choices))

    async def _send_document(self, path: str, filename: str, caption: str) -> None:
        await self._channel.send(content=caption or None, file=discord.File(path, filename=filename))


class DiscordClient:
    """Owns per-user sessions and routes Discord events to the generic flow."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # privileged intent — enable it in the Dev Portal
        self.bot = discord.Client(intents=intents)
        self._sessions: dict[int, Session] = {}
        self.bot.event(self.on_ready)
        self.bot.event(self.on_message)

    # -- helpers ------------------------------------------------------------
    def _session(self, uid: int) -> Session:
        s = self._sessions.get(uid)
        if s is None:
            s = Session(f"discord:{uid}")
            self._sessions[uid] = s
        return s

    @staticmethod
    def _authorized(uid: int) -> bool:
        return uid in config.DISCORD_ALLOWED_IDS

    def _start_flow(self, session: Session, coro) -> None:
        session.cancel()
        session.handle = None
        session._clear()
        session.task = asyncio.create_task(self._run(coro))

    @staticmethod
    async def _run(coro) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Unhandled error in flow")

    # -- events -------------------------------------------------------------
    async def on_ready(self) -> None:
        logger.info(f"Discord connecté en tant que {self.bot.user}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        uid = message.author.id
        if not self._authorized(uid):
            return
        content = (message.content or "").strip()
        if not content:
            return
        s = self._session(uid)
        low = content.lower()
        if low in ("/start", "!start"):
            self._start_flow(s, flow.run_start(DiscordContext(s, self, message.channel)))
            return
        if low in ("/settings", "!settings"):
            self._start_flow(s, flow.run_settings(DiscordContext(s, self, message.channel)))
            return
        # Feed a pending text prompt (email/Kindle); otherwise it's a new search.
        if s.is_waiting() and s.resolve_text(content):
            return
        self._start_flow(s, flow.run_search(DiscordContext(s, self, message.channel), content))

    async def _on_button(self, interaction: discord.Interaction, value: str) -> None:
        uid = interaction.user.id
        if not self._authorized(uid):
            await interaction.response.defer()
            return
        s = self._session(uid)
        # Only honour clicks on the current interaction message (ignore stale buttons).
        if s.handle is None or interaction.message is None or interaction.message.id != s.handle.id:
            await interaction.response.defer()
            return
        await interaction.response.defer()  # ack within 3s; the flow edits the message next
        if value == CANCEL:
            s.cancel()
            with contextlib.suppress(Exception):
                await interaction.message.edit(content="⛔ Annulé.", view=None)
            return
        s.resolve_choice(value)

    # -- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        await self.bot.start(config.DISCORD_TOKEN)

    async def close(self) -> None:
        await self.bot.close()
