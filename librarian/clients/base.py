"""The client port and generic session machinery.

The conversation flow (see flow.py) talks only to ``ClientContext`` and never to
any messaging platform. A platform adapter subclasses ``ClientContext`` and
implements the rendering primitives + ``max_file_size``; it also routes incoming
platform events into ``Session.resolve_text`` / ``resolve_choice`` / ``cancel``.

Interaction model: **conversation**. Each ``say``/``ask_*`` posts a NEW message and
disables the buttons of the previous prompt, so the history stays readable. The one
exception is ``update_status`` (download progress), which edits a single live message
in place.

Content can be a plain ``str`` or a rich ``Card`` (rendered as a Discord embed, or as
formatted text on Telegram). ``Choice`` may carry a description/emoji so adapters can
render richer pickers (e.g. a Discord select menu for long result lists).
"""

from __future__ import annotations

import abc
import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any

# Reserved choice values interpreted by the machinery, not by the flow.
CANCEL = "__cancel__"
SKIP = "__skip__"


@dataclass
class Choice:
    """A selectable option. ``value`` is opaque to the platform."""

    label: str
    value: str
    description: str = ""   # secondary line (Discord select option / appended on Telegram)
    emoji: str = ""         # optional leading emoji


@dataclass
class Card:
    """Rich content: a Discord embed, or formatted text elsewhere."""

    title: str = ""
    description: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)  # (name, value)
    thumbnail: str | None = None   # image URL (e.g. a book cover)
    footer: str = ""
    color: int | None = None       # embed accent colour


Content = str | Card


@dataclass
class Session:
    """Per-user conversation state, platform-neutral."""

    user_key: str
    data: dict = field(default_factory=dict)          # ephemeral per-user state (rate limit…)
    handle: Any = None                                # the message that currently owns buttons
    live_active: bool = False                         # True while `handle` is the live progress msg
    task: asyncio.Task | None = None                  # the running flow coroutine
    _pending: asyncio.Future | None = None
    _pending_kind: str | None = None                  # "choice" | "text"
    _allowed: set[str] = field(default_factory=set)   # button values that also resolve a text wait

    def park(self, kind: str, allowed: set[str] | None = None) -> asyncio.Future:
        fut = asyncio.get_event_loop().create_future()
        self._pending = fut
        self._pending_kind = kind
        self._allowed = allowed or set()
        return fut

    def _clear(self) -> None:
        self._pending = None
        self._pending_kind = None
        self._allowed = set()

    def is_waiting(self) -> bool:
        return self._pending is not None and not self._pending.done()

    def resolve_text(self, text: str) -> bool:
        if self.is_waiting() and self._pending_kind == "text":
            self._pending.set_result(text)
            self._clear()
            return True
        return False

    def resolve_choice(self, value: str) -> bool:
        if not self.is_waiting():
            return False
        if self._pending_kind == "choice" or (self._pending_kind == "text" and value in self._allowed):
            self._pending.set_result(value)
            self._clear()
            return True
        return False

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()


class ClientContext(abc.ABC):
    """What the generic flow is allowed to do. Subclassed per platform."""

    def __init__(self, session: Session):
        self.session = session

    # -- identity / limits --------------------------------------------------
    @property
    def user_key(self) -> str:
        return self.session.user_key

    @property
    def data(self) -> dict:
        return self.session.data

    @property
    @abc.abstractmethod
    def max_file_size(self) -> int:
        """Platform upload limit in bytes (Telegram 50 MB, Discord ~25 MB…)."""

    # -- rendering primitives (platform-specific) ---------------------------
    @abc.abstractmethod
    async def _send(self, content: Content, choices: list[Choice] | None = None) -> Any:
        """Send a new message; return an opaque handle."""

    @abc.abstractmethod
    async def _edit(self, handle: Any, content: Content, choices: list[Choice] | None = None) -> None:
        """Edit a previously sent message in place."""

    @abc.abstractmethod
    async def _disable(self, handle: Any) -> None:
        """Remove the buttons/components from a message, keeping its content."""

    @abc.abstractmethod
    async def _send_document(self, path: str, filename: str, caption: str) -> None:
        """Upload a file to the user."""

    # -- flow-facing API (generic, conversation model) ----------------------
    async def _finalize(self) -> None:
        """Close the current interactive message (strip its buttons)."""
        if self.session.handle is not None:
            with contextlib.suppress(Exception):
                await self._disable(self.session.handle)
        self.session.handle = None
        self.session.live_active = False

    async def say(self, content: Content) -> None:
        """Post a new informational message (ends any pending prompt above it)."""
        await self._finalize()
        await self._send(content)

    async def update_status(self, content: Content, choices: list[Choice] | None = None) -> None:
        """Download progress: keep editing ONE live message in place (best-effort)."""
        with contextlib.suppress(Exception):
            if not self.session.live_active:
                await self._finalize()
                self.session.handle = await self._send(content, choices)
                self.session.live_active = True
            else:
                await self._edit(self.session.handle, content, choices)

    async def ask_choice(self, prompt: Content, choices: list[Choice], cancellable: bool = True) -> str:
        opts = list(choices)
        if cancellable:
            opts.append(Choice("⛔ Annuler", CANCEL))
        await self._finalize()
        fut = self.session.park("choice")
        self.session.handle = await self._send(prompt, opts)
        return await fut

    async def ask_text(self, prompt: Content, buttons: list[Choice] | None = None, cancellable: bool = True) -> str:
        opts = list(buttons or [])
        if cancellable:
            opts.append(Choice("⛔ Annuler", CANCEL))
        allowed = {b.value for b in (buttons or [])}
        await self._finalize()
        fut = self.session.park("text", allowed)
        self.session.handle = await self._send(prompt, opts or None)
        return await fut

    async def send_document(self, path: str, filename: str, caption: str) -> None:
        await self._send_document(path, filename, caption)
