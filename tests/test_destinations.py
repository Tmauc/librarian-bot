"""Locks for the destination seam — pluggable delivery targets."""

import asyncio

from librarian.core import delivery, prefs
from librarian.destinations import registry
from librarian.destinations.base import Destination
from librarian.destinations.here import ThisChatDestination


class FakeCtx:
    def __init__(self, user_key="telegram:1"):
        self.user_key = user_key
        self.said = []
        self.docs = []

    async def say(self, text, choices=None):
        self.said.append(text)

    async def send_document(self, path, filename, caption):
        self.docs.append((filename, caption))


def _fake_prefs(data):
    async def _get(user_key):
        return data

    return _get


def test_registry_defaults():
    assert [d.name for d in registry.all_destinations()] == ["here", "email", "kindle"]
    assert registry.get("email").name == "email"
    assert registry.get("nope") is None


def test_here_is_always_available_and_sends_a_document():
    ctx = FakeCtx()
    d = ThisChatDestination()
    assert asyncio.run(d.available(ctx)) is True
    asyncio.run(d.deliver(ctx, "/tmp/x.epub", "Book.epub", "Book", " ⚠️vt"))
    assert ctx.docs == [("Book.epub", "📖 Book ⚠️vt")]
    assert "Envoyé" in ctx.said[-1]


def test_mail_availability_requires_smtp_and_address(monkeypatch):
    ctx = FakeCtx()
    email = registry.get("email")

    monkeypatch.setattr(delivery, "is_configured", lambda: False)
    monkeypatch.setattr(prefs, "get", _fake_prefs({"email": "a@b.co"}))
    assert asyncio.run(email.available(ctx)) is False  # SMTP off

    monkeypatch.setattr(delivery, "is_configured", lambda: True)
    assert asyncio.run(email.available(ctx)) is True  # SMTP on + address

    monkeypatch.setattr(prefs, "get", _fake_prefs({}))
    assert asyncio.run(email.available(ctx)) is False  # no address on file


def test_kindle_delivery_uses_the_convert_flag(monkeypatch):
    ctx = FakeCtx()
    sent = {}

    async def fake_send(path, filename, addr, kindle=False):
        sent.update(path=path, addr=addr, kindle=kindle)

    monkeypatch.setattr(prefs, "get", _fake_prefs({"kindle_email": "me@kindle.com"}))
    monkeypatch.setattr(delivery, "send_file", fake_send)
    asyncio.run(registry.get("kindle").deliver(ctx, "/tmp/x.epub", "B.epub", "B", ""))
    assert sent == {"path": "/tmp/x.epub", "addr": "me@kindle.com", "kindle": True}
    assert "Envoyé" in ctx.said[-1]


def test_available_for_falls_back_to_here(monkeypatch):
    monkeypatch.setattr(delivery, "is_configured", lambda: False)
    monkeypatch.setattr(prefs, "get", _fake_prefs({}))
    avail = asyncio.run(registry.available_for(FakeCtx()))
    assert [d.name for d in avail] == ["here"]


def test_adding_a_destination_needs_no_core_change():
    saved = list(registry._ALL)

    class FolderDestination(Destination):
        name = "folder"
        label = "📁 Dossier"

        async def deliver(self, ctx, path, filename, title, caption):
            pass

    registry.register(FolderDestination())
    try:
        assert registry.get("folder") is not None
        assert "folder" in [d.name for d in registry.all_destinations()]
    finally:
        registry._ALL[:] = saved
