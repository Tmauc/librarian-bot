"""Locks for the Discord adapter — the second platform, proving the seam.

Building a discord.Client opens no network connection, so these are safe unit tests.
"""

import asyncio

from librarian import config
from librarian.clients.base import CANCEL, Choice
from librarian.clients.discord.adapter import DiscordClient, _FlowView, _MultiSelectView


def _build_view(choices):
    # discord.ui.View.__init__ needs a running loop (it creates a Future).
    async def build():
        return _FlowView(DiscordClient(), choices)

    return asyncio.run(build())


def test_multiselect_view_is_a_native_multi_select():
    async def build():
        return _MultiSelectView(DiscordClient(), [Choice(f"Tome {i}", str(i), description="x") for i in range(4)], True)

    view = asyncio.run(build())
    selects = [c for c in view.children if type(c).__name__ == "Select"]
    buttons = [c for c in view.children if type(c).__name__ == "Button"]
    assert len(selects) == 1
    # min_values=0 so the user can untick everything; picking != validating.
    assert selects[0].min_values == 0 and selects[0].max_values == 4
    assert len(selects[0].options) == 4
    labels = [b.label for b in buttons]
    assert any("Tout cocher" in label for label in labels)   # pre-check all, no submit
    assert any("Valider" in label for label in labels)       # explicit validate step
    assert any(b.custom_id == CANCEL for b in buttons)


def test_multiselect_select_all_prechecks_without_submitting(monkeypatch):
    """« Tout cocher » ticks every option (default=True) and re-renders — it must NOT
    resolve the flow, so the user can still untick a few before validating."""
    resolved = []

    class _StubClient:
        @staticmethod
        def _authorized(uid):
            return True

        async def _on_multi(self, interaction, values):
            resolved.append(values)

    class _Resp:
        async def edit_message(self, **kw):
            pass

        async def defer(self):
            pass

    async def run():
        view = _MultiSelectView(_StubClient(), [Choice(f"Tome {i}", str(i)) for i in range(3)], True)
        inter = type("I", (), {"user": type("U", (), {"id": 1})(), "response": _Resp(), "data": {}})()
        await view._on_all(inter)
        return view

    view = asyncio.run(run())
    assert view._selected == {"0", "1", "2"}          # all pre-checked
    assert resolved == []                             # but NOT submitted
    select = next(c for c in view.children if type(c).__name__ == "Select")
    assert all(o.default for o in select.options)     # ticks reflected in the dropdown


def test_flowview_maps_choices_to_button_custom_ids():
    view = _build_view([Choice("Oui", "yes"), Choice("Non", "no"), Choice("⛔ Annuler", CANCEL)])
    buttons = [(b.label, b.custom_id) for b in view.children]
    assert buttons == [("Oui", "yes"), ("Non", "no"), ("⛔ Annuler", CANCEL)]


def test_flowview_lays_out_more_than_five_choices_in_rows():
    view = _build_view([Choice(f"r{i}", str(i)) for i in range(7)])
    rows = {b.row for b in view.children}
    # 7 buttons must span at least 2 action rows (max 5 per row).
    assert rows == {0, 1}


def test_authorized(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ALLOWED_IDS", {42})
    assert DiscordClient._authorized(42) is True
    assert DiscordClient._authorized(99) is False


def test_session_key_is_discord_namespaced():
    client = DiscordClient()
    s = client._session(123)
    assert s.user_key == "discord:123"
    assert client._session(123) is s  # same session reused


def test_unauthorized_message_starts_no_flow(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ALLOWED_IDS", {42})
    client = DiscordClient()

    message = type(
        "M",
        (),
        {
            "author": type("A", (), {"id": 999, "bot": False})(),
            "content": "dune",
            "channel": object(),
        },
    )()
    asyncio.run(client.on_message(message))
    assert client._sessions == {}, "an unauthorized user must not create a session"


def test_bot_messages_are_ignored(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_ALLOWED_IDS", {42})
    client = DiscordClient()
    message = type(
        "M",
        (),
        {"author": type("A", (), {"id": 42, "bot": True})(), "content": "dune", "channel": object()},
    )()
    asyncio.run(client.on_message(message))
    assert client._sessions == {}
