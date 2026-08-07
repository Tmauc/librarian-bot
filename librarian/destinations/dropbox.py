"""Dropbox destination — uploads the file to a Dropbox folder (single account).

A Kobo (or any e-reader) pointed at the same Dropbox folder then syncs the book.
Auth is a single app + long-lived refresh token in the environment; no per-user
OAuth. See docs/destinations.md for how to obtain the credentials.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import httpx

from librarian import config
from librarian.destinations.base import CloudUploadDestination

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext

_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
_UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class DropboxDestination(CloudUploadDestination):
    name = "dropbox"
    label = "☁️ Dropbox"
    where = "Dropbox"

    async def available(self, ctx: ClientContext) -> bool:
        return bool(config.DROPBOX_REFRESH_TOKEN and config.DROPBOX_APP_KEY and config.DROPBOX_APP_SECRET)

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            _TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": config.DROPBOX_REFRESH_TOKEN},
            auth=(config.DROPBOX_APP_KEY, config.DROPBOX_APP_SECRET),
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def _upload(self, path: str, filename: str) -> None:
        folder = (config.DROPBOX_FOLDER or "").rstrip("/")
        # Dropbox-API-Arg must be an ASCII header; ensure_ascii escapes any accents.
        arg = json.dumps(
            {"path": f"{folder}/{filename}", "mode": "overwrite", "mute": True, "autorename": True},
            ensure_ascii=True,
        )
        async with httpx.AsyncClient(timeout=90) as client:
            access = await self._access_token(client)
            data = await asyncio.get_event_loop().run_in_executor(None, _read_bytes, path)
            resp = await client.post(
                _UPLOAD_URL,
                content=data,
                headers={
                    "Authorization": f"Bearer {access}",
                    "Dropbox-API-Arg": arg,
                    "Content-Type": "application/octet-stream",
                },
            )
            resp.raise_for_status()
