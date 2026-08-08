"""Locks for the destination seam — pluggable delivery targets."""

import asyncio
import json

from librarian import config
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
    assert [d.name for d in registry.all_destinations()] == ["here", "email", "kindle", "dropbox", "gdrive"]
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


# --- cloud destinations (Dropbox / Google Drive) ---------------------------
class _FakeResp:
    def __init__(self, data=None):
        self._data = data or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeHTTP:
    def __init__(self, responder, calls):
        self._responder = responder
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self._calls.append({"m": "GET", "url": url, **kw})
        return _FakeResp(self._responder("GET", url))

    async def post(self, url, **kw):
        self._calls.append({"m": "POST", "url": url, **kw})
        return _FakeResp(self._responder("POST", url))

    async def patch(self, url, **kw):
        self._calls.append({"m": "PATCH", "url": url, **kw})
        return _FakeResp(self._responder("PATCH", url))


def _fake_httpx(monkeypatch, module, mapping):
    """``mapping`` is either a {url-substring: data} dict (method-agnostic) or a
    ``responder(method, url) -> data`` callable for finer control."""
    calls = []

    if callable(mapping):
        responder = mapping
    else:
        def responder(method, url):
            for key, val in mapping.items():
                if key in url:
                    return val
            return {}

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda *a, **k: _FakeHTTP(responder, calls))
    return calls


def test_dropbox_available_requires_credentials(monkeypatch):
    from librarian.destinations.dropbox import DropboxDestination

    d = DropboxDestination()
    monkeypatch.setattr(config, "DROPBOX_REFRESH_TOKEN", "")
    assert asyncio.run(d.available(FakeCtx())) is False
    monkeypatch.setattr(config, "DROPBOX_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(config, "DROPBOX_APP_KEY", "k")
    monkeypatch.setattr(config, "DROPBOX_APP_SECRET", "s")
    assert asyncio.run(d.available(FakeCtx())) is True


def test_dropbox_upload_puts_file_in_folder(monkeypatch, tmp_path):
    from librarian.destinations import dropbox as dbx

    monkeypatch.setattr(config, "DROPBOX_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(config, "DROPBOX_APP_KEY", "k")
    monkeypatch.setattr(config, "DROPBOX_APP_SECRET", "s")
    monkeypatch.setattr(config, "DROPBOX_FOLDER", "/Kobo")
    f = tmp_path / "Book.epub"
    f.write_bytes(b"hello")
    calls = _fake_httpx(monkeypatch, dbx, {"/token": {"access_token": "AT"}})

    ctx = FakeCtx()
    asyncio.run(dbx.DropboxDestination().deliver(ctx, str(f), "Book.epub", "Book", ""))

    upload = next(c for c in calls if c["url"].endswith("/files/upload"))
    assert upload["headers"]["Authorization"] == "Bearer AT"
    assert json.loads(upload["headers"]["Dropbox-API-Arg"])["path"] == "/Kobo/Book.epub"
    assert upload["content"] == b"hello"
    assert "Déposé" in ctx.said[-1]


# --- folder organisation (sort schemes) ------------------------------------
def test_subfolders_by_scheme():
    from librarian.core.metadata import BookMeta
    from librarian.destinations import base

    m = BookMeta(author="Anne Robillard", series="Les Chevaliers d'Émeraude", index=1)
    assert base.subfolders("author_series", m) == ["Anne Robillard", "Les Chevaliers d'Émeraude"]
    assert base.subfolders("author", m) == ["Anne Robillard"]
    assert base.subfolders("series", m) == ["Les Chevaliers d'Émeraude"]
    assert base.subfolders("flat", m) == []
    # no series → author_series collapses to just the author
    assert base.subfolders("author_series", BookMeta(author="Tolkien")) == ["Tolkien"]
    # no author → fallback bucket; a "/" in a name never escapes the folder
    assert base.subfolders("author", BookMeta()) == ["Sans auteur"]
    assert base.subfolders("author", BookMeta(author="AC/DC")) == ["AC DC"]


def test_dropbox_upload_uses_subfolders(monkeypatch, tmp_path):
    from librarian.core import prefs
    from librarian.core.metadata import BookMeta
    from librarian.destinations import dropbox as dbx

    for k, v in (("DROPBOX_REFRESH_TOKEN", "rt"), ("DROPBOX_APP_KEY", "k"),
                 ("DROPBOX_APP_SECRET", "s"), ("DROPBOX_FOLDER", "/Kobo")):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(prefs, "get", _fake_prefs({"sort_scheme": "author_series"}))
    f = tmp_path / "Book.epub"
    f.write_bytes(b"hello")
    calls = _fake_httpx(monkeypatch, dbx, {"/token": {"access_token": "AT"}})

    meta = BookMeta(author="Anne Robillard", series="Les Chevaliers d'Émeraude", index=1)
    asyncio.run(dbx.DropboxDestination().deliver(FakeCtx(), str(f), "01 - Le Feu.epub", "Le Feu", "", meta=meta))

    upload = next(c for c in calls if c["url"].endswith("/files/upload"))
    path = json.loads(upload["headers"]["Dropbox-API-Arg"])["path"]
    assert path == "/Kobo/Anne Robillard/Les Chevaliers d'Émeraude/01 - Le Feu.epub"


def test_gdrive_upload_creates_folder_chain(monkeypatch, tmp_path):
    from librarian.core import prefs
    from librarian.core.metadata import BookMeta
    from librarian.destinations import gdrive as gd

    for k, v in (("GDRIVE_REFRESH_TOKEN", "rt"), ("GDRIVE_CLIENT_ID", "id"),
                 ("GDRIVE_CLIENT_SECRET", "sec"), ("GDRIVE_FOLDER_ID", "ROOT")):
        monkeypatch.setattr(config, k, v)
    monkeypatch.setattr(prefs, "get", _fake_prefs({"sort_scheme": "author_series"}))
    f = tmp_path / "Book.epub"
    f.write_bytes(b"hi")

    def responder(method, url):
        if "/token" in url:
            return {"access_token": "AT"}
        if "/upload/drive" in url:
            return {"id": "FID"}          # media upload
        if method == "GET":
            return {"files": []}          # folder not found → force create
        if method == "POST":
            return {"id": "NEWFOLDER"}    # folder create
        return {}

    calls = _fake_httpx(monkeypatch, gd, responder)
    meta = BookMeta(author="Anne Robillard", series="Les Chevaliers d'Émeraude", index=1)
    asyncio.run(gd.GoogleDriveDestination().deliver(FakeCtx(), str(f), "01 - Le Feu.epub", "Le Feu", "", meta=meta))

    created = [c for c in calls if c["m"] == "POST" and c["url"].endswith("/drive/v3/files")]
    assert [c["json"]["name"] for c in created] == ["Anne Robillard", "Les Chevaliers d'Émeraude"]
    patch = next(c for c in calls if c["m"] == "PATCH")
    assert patch["params"]["addParents"] == "NEWFOLDER"  # filed into the deepest folder


def test_gdrive_available_requires_credentials(monkeypatch):
    from librarian.destinations.gdrive import GoogleDriveDestination

    d = GoogleDriveDestination()
    monkeypatch.setattr(config, "GDRIVE_REFRESH_TOKEN", "")
    assert asyncio.run(d.available(FakeCtx())) is False
    monkeypatch.setattr(config, "GDRIVE_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(config, "GDRIVE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GDRIVE_CLIENT_SECRET", "sec")
    assert asyncio.run(d.available(FakeCtx())) is True


def test_gdrive_uploads_then_names_and_moves(monkeypatch, tmp_path):
    from librarian.destinations import gdrive as gd

    monkeypatch.setattr(config, "GDRIVE_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(config, "GDRIVE_CLIENT_ID", "id")
    monkeypatch.setattr(config, "GDRIVE_CLIENT_SECRET", "sec")
    monkeypatch.setattr(config, "GDRIVE_FOLDER_ID", "FOLDER")
    f = tmp_path / "Book.epub"
    f.write_bytes(b"hi")
    calls = _fake_httpx(monkeypatch, gd, {"/token": {"access_token": "AT"}, "/upload/drive": {"id": "FID"}})

    ctx = FakeCtx()
    asyncio.run(gd.GoogleDriveDestination().deliver(ctx, str(f), "Book.epub", "Book", ""))

    upload = next(c for c in calls if "/upload/drive" in c["url"])
    assert upload["content"] == b"hi"
    patch = next(c for c in calls if c["m"] == "PATCH")
    assert patch["url"].endswith("/files/FID")
    assert patch["json"] == {"name": "Book.epub"}
    assert patch["params"]["addParents"] == "FOLDER"
    assert "Déposé" in ctx.said[-1]
