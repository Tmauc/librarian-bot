# Destinations (where the file ends up)

A **destination** is where a downloaded (and [converted](architecture.md) / scanned) book is
finally sent. Destinations are a pluggable seam — the third one, alongside
[sources](sources/README.md) and [clients](clients/README.md). Adding one touches no core,
flow, source or client code.

The flow offers the user only the destinations **available** to them, then calls the chosen
one's `deliver()`.

## Built-in destinations

| Name | Label | Available when | How |
|---|---|---|---|
| `here` | 📬 Ici (ce chat) | always | the active client uploads the file (`ctx.send_document`) |
| `email` | 📧 Email | SMTP configured **and** the user has an email on file | plain SMTP attachment |
| `kindle` | 📖 Kindle | SMTP configured **and** the user has a Kindle address | SMTP with the `convert` subject (Send to Kindle) |
| `dropbox` | ☁️ Dropbox | Dropbox credentials configured | uploads to a Dropbox folder (a Kobo pointed at it syncs the book) |
| `gdrive` | ☁️ Google Drive | Google Drive credentials configured | uploads to a Drive folder (same idea for Kobo/other readers) |

The chat destination uses the active client's file-size limit — see
[client limits](clients/README.md#per-platform-upload-limits). Email/Kindle are platform-neutral.

## Cloud folder organisation

Cloud destinations (Dropbox, Google Drive) file each book into sub-folders derived from its
[clean metadata](intelligence.md) (author/series), chosen per user in `/settings` → **Rangement
cloud** (the `sort_scheme` pref):

| Scheme | Layout |
|---|---|
| `author_series` (default) | `Auteur/Série/01 - Titre.epub` |
| `author` | `Auteur/Titre.epub` |
| `series` | `Série/Titre.epub` (standalone books at the root) |
| `flat` | everything at the root (original behaviour) |

`CloudUploadDestination` computes the segments (`base.subfolders`), then the provider files the
book: **Dropbox** just uploads to the full path (folders auto-created); **Google Drive** resolves
or creates each folder id (cached per run) and sets it as the parent. Volumes in a series are
prefixed with their number (`01 - …`) so they sort in reading order. The sort option only appears
once a cloud destination is configured. Re-sorting an existing folder after a scheme change is a
planned on-demand action (not automatic).

## The contract: `Destination`

Defined in [`librarian/destinations/base.py`](../librarian/destinations/base.py).

| Member | Purpose |
|---|---|
| `name` | Unique registry name (also the menu choice value). |
| `label` | Button label shown to the user. |
| `available(ctx) -> bool` | Whether it can be offered to this user now (default `True`). |
| `deliver(ctx, path, filename, title, caption, meta=None)` | Send the file. `meta` is the book's clean metadata (author/series) for folder-organising destinations; plain ones ignore it. Owns its status messages and error handling. |

`MailDestination` in the same file is a reusable base for SMTP-backed destinations
(email, Kindle differ only by stored-address key, `convert` flag, and wording).

## <a name="adding-a-destination"></a>Adding a destination

1. Create `librarian/destinations/<name>.py` with a `Destination` subclass. Read what you need
   from `ctx` (chat) and [`core.prefs`](../librarian/core/prefs.py) (stored addresses); use
   [`core.delivery`](../librarian/core/delivery.py) for SMTP if relevant. Show progress with
   `ctx.say(...)` and handle your own errors.
2. Add it to `_ALL` in [`librarian/destinations/registry.py`](../librarian/destinations/registry.py)
   (or call `register()`).
3. Gate it in `available()` (e.g. require a config value or a stored address).
4. Add a row above and, if it needs config, document it in [configuration](configuration.md).
5. Add tests — see `tests/test_destinations.py`.

Ideas that fit this seam without any core change: a local-folder save, a Dropbox/Google Drive
upload, a webhook, or an S3 bucket.

## SMTP setup (email & Send to Kindle)

SMTP is configured globally in `.env`; each user sets their own address via `/settings`.

| Variable | Description |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` | Server (default `smtp.gmail.com` / `587`, STARTTLS). |
| `SMTP_USER` / `SMTP_PASSWORD` | Login. Gmail: use an **App Password** ([myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)). |
| `SMTP_FROM` | Sender address (defaults to `SMTP_USER`). |

**Send to Kindle:** add `SMTP_FROM` to your Amazon *Approved Personal Document E-mail List*
(Manage Your Content and Devices → Preferences → Personal Document Settings). Amazon converts
the file, so [Calibre](../README.md) is not needed for Kindle.

## Cloud destinations setup (Dropbox / Google Drive)

These upload the book to a folder in **one** cloud account (configured in `.env`, no
per-user OAuth). Point your e-reader at the same folder — a **Kobo** (incl. the Clara
Colour) has built-in Dropbox/Google Drive sync, so the book appears wirelessly. They
only show in the menu once their credentials are set.

### Dropbox

Single app + a long-lived refresh token:

1. Create an app at the [Dropbox App Console](https://www.dropbox.com/developers/apps)
   (Scoped access, App folder or Full Dropbox). Note the **App key** and **App secret**.
2. Give it the `files.content.write` permission.
3. Generate a **refresh token** (OAuth `token_access_type=offline`). A quick way: run the
   authorize URL, get a `code`, then exchange it once for a refresh token.
4. Fill in `.env`:
   ```
   DROPBOX_APP_KEY=...
   DROPBOX_APP_SECRET=...
   DROPBOX_REFRESH_TOKEN=...
   DROPBOX_FOLDER=/Kobo        # where files land (must exist or be auto-created)
   ```

| Variable | Description |
|---|---|
| `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET` | From the Dropbox app. |
| `DROPBOX_REFRESH_TOKEN` | Long-lived OAuth refresh token. |
| `DROPBOX_FOLDER` | Target folder path (default `/librarian-bot`). |

### Google Drive

1. In [Google Cloud Console](https://console.cloud.google.com/): create OAuth client
   credentials (Desktop), enable the **Drive API**. Note the **Client ID/Secret**.
2. Obtain a **refresh token** for the `drive.file` scope (OAuth Playground or a one-off script).
3. Get the destination **folder ID** (from the folder's URL). Fill in `.env`:
   ```
   GDRIVE_CLIENT_ID=...
   GDRIVE_CLIENT_SECRET=...
   GDRIVE_REFRESH_TOKEN=...
   GDRIVE_FOLDER_ID=...        # optional; omit for Drive root
   ```

Both are implemented with plain httpx (no extra dependency): refresh the access token,
then upload. See [`dropbox.py`](../librarian/destinations/dropbox.py) /
[`gdrive.py`](../librarian/destinations/gdrive.py).

## VirusTotal scan (optional)

If `VIRUSTOTAL_API_KEY` is set, the file is scanned before any destination
([`core/scanning.py`](../librarian/core/scanning.py)): checked by SHA-256 first, else uploaded
and polled. **Malicious** files are blocked; **suspicious** files are sent with a caption
warning; files over 32 MB are skipped.

## E-readers: which format for which device

The bot delivers the file; you load it onto the device. Pick the format accordingly
(`ALLOWED_FORMATS` in [configuration](configuration.md)).

| Device | EPUB | PDF | MOBI | AZW3 | Wireless option |
|---|---|---|---|---|---|
| **Kindle** (2022+) | ✅ | ✅ | ✅ | ✅ | Send to Kindle (the `kindle` destination) |
| **Kindle** (pre-2022) | ❌ | ✅ | ✅ | ✅ | Send to Kindle |
| **Kobo** (incl. Clara Colour) | ✅ (best; colour via EPUB3) | ✅ | ✅ | ❌ | built-in Dropbox / Google Drive sync, or USB |

Notes for **Kobo**: EPUB is native and the only format that uses the Clara Colour's colour
screen; **AZW3 is the only unsupported format**. There is no email-to-device endpoint like
Send to Kindle — instead use the **`dropbox`** or **`gdrive`** destination (see setup above):
the bot drops the book into a cloud folder and the Kobo's built-in Dropbox/Google Drive sync
picks it up wirelessly. USB transfer also works.
