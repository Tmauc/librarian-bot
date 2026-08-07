"""Telegram adapter — the ONLY Telegram-specific code.

Responsibilities:
- render the generic port (ClientContext) onto python-telegram-bot primitives,
- route incoming updates into the per-user Session (resolve pending futures /
  start flows / cancel),
- enforce the Telegram whitelist and file-size limit.

Everything about *what* the conversation does lives in ``librarian.clients.flow``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from librarian import config
from librarian.clients import flow
from librarian.clients.base import CANCEL, Choice, ClientContext, Session

logger = logging.getLogger(__name__)


class TelegramContext(ClientContext):
    """Renders the generic port onto a single Telegram chat."""

    def __init__(self, session: Session, bot, chat_id: int):
        super().__init__(session)
        self._bot = bot
        self._chat_id = chat_id

    @property
    def max_file_size(self) -> int:
        return config.TELEGRAM_MAX_FILE_SIZE

    @staticmethod
    def _kb(choices: list[Choice] | None):
        if not choices:
            return None
        # Choice.value is short (< 64 bytes) for all flow choices, so it doubles as
        # the callback_data — no separate token table needed.
        return InlineKeyboardMarkup([[InlineKeyboardButton(c.label, callback_data=c.value)] for c in choices])

    async def _send(self, text: str, choices: list[Choice] | None = None):
        return await self._bot.send_message(self._chat_id, text, reply_markup=self._kb(choices))

    async def _edit(self, handle, text: str, choices: list[Choice] | None = None) -> None:
        try:
            await self._bot.edit_message_text(
                text, chat_id=handle.chat_id, message_id=handle.message_id, reply_markup=self._kb(choices)
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg or "message to edit not found" in msg:
                return  # benign for our evolving-message UX
            raise

    async def _send_document(self, path: str, filename: str, caption: str) -> None:
        with open(path, "rb") as f:
            await self._bot.send_document(self._chat_id, document=f, filename=filename, caption=caption)


class TelegramClient:
    """Owns per-user sessions and routes Telegram updates to the generic flow."""

    def __init__(self):
        self._sessions: dict[int, Session] = {}

    # -- helpers ------------------------------------------------------------
    def _session(self, uid: int) -> Session:
        s = self._sessions.get(uid)
        if s is None:
            s = Session(f"telegram:{uid}")
            self._sessions[uid] = s
        return s

    @staticmethod
    def _authorized(update: Update) -> bool:
        u = update.effective_user
        return bool(u and u.id in config.TELEGRAM_ALLOWED_IDS)

    def _start_flow(self, session: Session, coro) -> None:
        session.cancel()          # abandon any running flow for this user
        session.handle = None     # next say() opens a fresh evolving message
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

    # -- handlers -----------------------------------------------------------
    async def on_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        s = self._session(update.effective_user.id)
        ctx = TelegramContext(s, context.bot, update.effective_chat.id)
        self._start_flow(s, flow.run_start(ctx))

    async def on_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        s = self._session(update.effective_user.id)
        ctx = TelegramContext(s, context.bot, update.effective_chat.id)
        self._start_flow(s, flow.run_settings(ctx))

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._authorized(update):
            return
        s = self._session(update.effective_user.id)
        text = update.message.text or ""
        # Feed a pending text prompt (email/Kindle); otherwise it's a new search.
        if s.is_waiting() and s.resolve_text(text):
            return
        ctx = TelegramContext(s, context.bot, update.effective_chat.id)
        self._start_flow(s, flow.run_search(ctx, text))

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        if not self._authorized(update):
            return
        s = self._session(update.effective_user.id)
        # Only honour taps on the current interaction message (ignore stale buttons).
        if s.handle is None or query.message is None or query.message.message_id != s.handle.message_id:
            return
        value = query.data or ""
        if value == CANCEL:
            s.cancel()
            with contextlib.suppress(Exception):
                await query.edit_message_text("⛔ Annulé.")
            return
        s.resolve_choice(value)
