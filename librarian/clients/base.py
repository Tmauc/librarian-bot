"""The client port and generic session machinery.

The conversation flow (see flow.py) talks only to ``ClientContext`` and never to
any messaging platform. A platform adapter subclasses ``ClientContext`` and
implements the four rendering primitives + ``max_file_size``; it also routes
incoming platform events into ``Session.resolve_text`` / ``resolve_choice`` /
``cancel``. That is the entirety of what a new platform must provide.
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
    """A selectable option. ``value`` is opaque to the platform (the adapter maps
    it to whatever short token its buttons need)."""

    label: str
    value: str


@dataclass
class Session:
    """Per-user conversation state, platform-neutral."""

    user_key: str
    data: dict = field(default_factory=dict)          # ephemeral per-user state (rate limit…)
    handle: Any = None                                # the current, in-place-edited message
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


class Cancelled(Exception):
    """Internal: the user cancelled the current interaction."""


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
    async def _send(self, text: str, choices: list[Choice] | None = None) -> Any:
        """Send a new message; return an opaque handle."""

    @abc.abstractmethod
    async def _edit(self, handle: Any, text: str, choices: list[Choice] | None = None) -> None:
        """Edit a previously sent message in place."""

    @abc.abstractmethod
    async def _send_document(self, path: str, filename: str, caption: str) -> None:
        """Upload a file to the user."""

    # -- flow-facing API (generic) ------------------------------------------
    async def say(self, text: str, choices: list[Choice] | None = None) -> None:
        """Show text on the single evolving interaction message (send then edit)."""
        if self.session.handle is None:
            self.session.handle = await self._send(text, choices)
        else:
            await self._edit(self.session.handle, text, choices)

    async def update_status(self, text: str, choices: list[Choice] | None = None) -> None:
        """Like say() but never raises (progress updates are best-effort)."""
        with contextlib.suppress(Exception):
            await self.say(text, choices)

    async def ask_choice(self, prompt: str, choices: list[Choice], cancellable: bool = True) -> str:
        opts = list(choices)
        if cancellable:
            opts.append(Choice("⛔ Annuler", CANCEL))
        fut = self.session.park("choice")
        await self.say(prompt, opts)
        return await fut

    async def ask_text(self, prompt: str, buttons: list[Choice] | None = None, cancellable: bool = True) -> str:
        opts = list(buttons or [])
        if cancellable:
            opts.append(Choice("⛔ Annuler", CANCEL))
        allowed = {b.value for b in (buttons or [])}
        fut = self.session.park("text", allowed)
        await self.say(prompt, opts or None)
        return await fut

    async def send_document(self, path: str, filename: str, caption: str) -> None:
        await self._send_document(path, filename, caption)
