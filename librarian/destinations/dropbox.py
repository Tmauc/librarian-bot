"""Dropbox destination — uploads the file to a Dropbox folder (single account).

A Kobo (or any e-reader) pointed at the same Dropbox folder then syncs the book.
Auth is a single app + long-lived refresh token in the environment; no per-user
OAuth. See docs/destinations.md for how to obtain the credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx

from librarian import config
from librarian.destinations.base import CloudUploadDestination

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
_UPLOAD_URL = "https://content.dropboxapi.com/2/files/upload"
_MOVE_URL = "https://api.dropboxapi.com/2/files/move_v2"


def _full_path(subfolders: list[str], filename: str) -> str:
    segments = [config.DROPBOX_FOLDER.strip("/")] if config.DROPBOX_FOLDER else []
    segments += [s.strip("/") for s in subfolders]
    segments.append(filename)
    return "/" + "/".join(s for s in segments if s)


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

    async def _upload(self, path: str, filename: str, subfolders: list[str]) -> dict:
        # Dropbox creates intermediate folders on upload — just build the full path.
        full_path = _full_path(subfolders, filename)
        # Dropbox-API-Arg must be an ASCII header; ensure_ascii escapes any accents.
        arg = json.dumps(
            {"path": full_path, "mode": "overwrite", "mute": True, "autorename": True},
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
        return {"path": resp.json().get("path_display", full_path)}

    async def _move_all(self, changes: list[tuple[dict, list[str]]]) -> None:
        async with httpx.AsyncClient(timeout=90) as client:
            access = await self._access_token(client)
            headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
            for rec, new_folders in changes:
                new_path = _full_path(new_folders, rec["filename"])
                if new_path == rec.get("path"):
                    rec["folders"] = new_folders
                    continue
                try:
                    resp = await client.post(
                        _MOVE_URL, headers=headers,
                        json={"from_path": rec["path"], "to_path": new_path, "autorename": True},
                    )
                    resp.raise_for_status()
                    rec["path"] = resp.json().get("metadata", {}).get("path_display", new_path)
                    rec["folders"] = new_folders
                except Exception as e:
                    logger.warning(f"Dropbox move failed for {rec.get('filename')!r}: {e}")
