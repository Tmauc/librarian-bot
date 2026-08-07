"""Locks for the Telegram adapter — the only platform-specific code.

Keeps the seam honest: keyboard mapping, whitelist gating, and event routing.
"""

import asyncio

from librarian import config
from librarian.clients.base import Choice, Session
from librarian.clients.telegram.adapter import TelegramClient, TelegramContext


def test_kb_uses_choice_value_as_callback_data():
    ctx = TelegramContext(Session("telegram:1"), bot=None, chat_id=1)
    kb = ctx._kb([Choice("Oui", "yes"), Choice("Non", "no")])
    buttons = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
    assert buttons == [("Oui", "yes"), ("Non", "no")]
    assert ctx._kb(None) is None


def test_authorized(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_IDS", {42})

    def upd(uid):
        return type("U", (), {"effective_user": type("E", (), {"id": uid})()})()

    assert TelegramClient._authorized(upd(42)) is True
    assert TelegramClient._authorized(upd(99)) is False
    assert TelegramClient._authorized(type("U", (), {"effective_user": None})()) is False


def test_unauthorized_text_starts_no_session(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_ALLOWED_IDS", {42})
    client = TelegramClient()

    update = type(
        "U",
        (),
        {
            "effective_user": type("E", (), {"id": 999})(),
            "effective_chat": type("C", (), {"id": 999})(),
            "message": type("M", (), {"text": "dune"})(),
        },
    )()
    asyncio.run(client.on_text(update, context=type("Ctx", (), {"bot": None})()))
    assert client._sessions == {}, "an unauthorized user must not create a session"
