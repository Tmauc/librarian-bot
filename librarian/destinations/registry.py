"""Destination registry — the single place to register where books can be sent.

To add a destination: create ``librarian/destinations/<name>.py`` with a
``Destination`` subclass, then add it to ``_ALL`` below (or call ``register()``).
No core, flow, source or client code changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from librarian.destinations.base import Destination
from librarian.destinations.dropbox import DropboxDestination
from librarian.destinations.email import EmailDestination
from librarian.destinations.gdrive import GoogleDriveDestination
from librarian.destinations.here import ThisChatDestination
from librarian.destinations.kindle import KindleDestination

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext

# Order = order shown in the menu. "here" first (always available); the cloud
# destinations only appear when their credentials are configured.
_ALL: list[Destination] = [
    ThisChatDestination(),
    EmailDestination(),
    KindleDestination(),
    DropboxDestination(),
    GoogleDriveDestination(),
]


def register(destination: Destination) -> None:
    _ALL.append(destination)


def all_destinations() -> list[Destination]:
    return list(_ALL)


def get(name: str) -> Destination | None:
    return next((d for d in _ALL if d.name == name), None)


async def available_for(ctx: ClientContext) -> list[Destination]:
    """Destinations offerable to this user right now (at least ``here``)."""
    out = []
    for d in _ALL:
        if await d.available(ctx):
            out.append(d)
    return out
