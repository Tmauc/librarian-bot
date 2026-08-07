"""Google Drive destination — uploads the file to a Drive folder (single account).

Like the Dropbox destination, this feeds an e-reader that syncs the same Drive
folder (e.g. a Kobo). Single OAuth client + refresh token in the environment.
See docs/destinations.md for how to obtain the credentials.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from librarian import config
from librarian.destinations.base import CloudUploadDestination

if TYPE_CHECKING:
    from librarian.clients.base import ClientContext

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=media&supportsAllDrives=true"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"


def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


class GoogleDriveDestination(CloudUploadDestination):
    name = "gdrive"
    label = "☁️ Google Drive"
    where = "Google Drive"

    async def available(self, ctx: ClientContext) -> bool:
        return bool(
            config.GDRIVE_REFRESH_TOKEN and config.GDRIVE_CLIENT_ID and config.GDRIVE_CLIENT_SECRET
        )

    async def _access_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": config.GDRIVE_REFRESH_TOKEN,
                "client_id": config.GDRIVE_CLIENT_ID,
                "client_secret": config.GDRIVE_CLIENT_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def _upload(self, path: str, filename: str) -> None:
        async with httpx.AsyncClient(timeout=90) as client:
            access = await self._access_token(client)
            headers = {"Authorization": f"Bearer {access}"}
            data = await asyncio.get_event_loop().run_in_executor(None, _read_bytes, path)

            # 1) upload the media, get a file id
            up = await client.post(
                _UPLOAD_URL, content=data, headers={**headers, "Content-Type": "application/octet-stream"}
            )
            up.raise_for_status()
            file_id = up.json()["id"]

            # 2) name it (and move it into the destination folder, if any)
            params = {"supportsAllDrives": "true"}
            if config.GDRIVE_FOLDER_ID:
                params["addParents"] = config.GDRIVE_FOLDER_ID
            patch = await client.patch(
                f"{_FILES_URL}/{file_id}",
                params=params,
                headers={**headers, "Content-Type": "application/json"},
                json={"name": filename},
            )
            patch.raise_for_status()
