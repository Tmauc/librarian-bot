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
import html
import logging

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from librarian import config
from librarian.clients import flow
from librarian.clients.base import CANCEL, Card, Choice, ClientContext, Content, Session

logger = logging.getLogger(__name__)

_notified_update: str | None = None


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
        # One button per row (stacks vertically → long titles stay readable).
        # Choice.value is short (< 64 bytes) so it doubles as the callback_data.
        rows = []
        for c in choices:
            label = (f"{c.emoji} " if c.emoji else "") + c.label
            rows.append([InlineKeyboardButton(label[:64], callback_data=c.value)])
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _render(content: Content):
        """Return (text, parse_mode). A Card becomes escaped HTML; a str is plain."""
        if isinstance(content, Card):
            parts = []
            if content.title:
                parts.append(f"<b>{html.escape(content.title)}</b>")
            if content.description:
                parts.append(html.escape(content.description))
            for name, value in content.fields:
                parts.append(f"<b>{html.escape(name)}</b> : {html.escape(value)}")
            if content.thumbnail:
                parts.append(f'🖼️ <a href="{html.escape(content.thumbnail, quote=True)}">Couverture</a>')
            if content.footer:
                parts.append(f"<i>{html.escape(content.footer)}</i>")
            return ("\n\n".join(parts) or "…"), "HTML"
        return content, None

    async def _send(self, content: Content, choices: list[Choice] | None = None):
        text, mode = self._render(content)
        return await self._bot.send_message(
            self._chat_id, text, parse_mode=mode,
            reply_markup=self._kb(choices), disable_web_page_preview=True,
        )

    async def _edit(self, handle, content: Content, choices: list[Choice] | None = None) -> None:
        text, mode = self._render(content)
        try:
            await self._bot.edit_message_text(
                text, chat_id=handle.chat_id, message_id=handle.message_id,
                parse_mode=mode, reply_markup=self._kb(choices), disable_web_page_preview=True,
            )
        except BadRequest as e:
            msg = str(e).lower()
            if "not modified" in msg or "message to edit not found" in msg:
                return  # benign for our evolving-message UX
            raise

    async def _disable(self, handle) -> None:
        """Remove the inline keyboard from a message (keeps its text)."""
        with contextlib.suppress(Exception):
            await self._bot.edit_message_reply_markup(
                chat_id=handle.chat_id, message_id=handle.message_id, reply_markup=None
            )

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


# ===========================================================================
# Telegram-specific extras: global error handler + update notifications
# ===========================================================================
async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception in handler", exc_info=context.error)
    try:
        if isinstance(update, Update) and update.effective_message is not None:
            await update.effective_message.reply_text(
                "😕 Une erreur inattendue est survenue. Réessaie dans un instant."
            )
    except Exception:
        pass


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


def create_application() -> Application:
    """Build a fully-wired python-telegram-bot Application (handlers, error handler,
    update-check job). The caller drives its lifecycle."""
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

    return app
