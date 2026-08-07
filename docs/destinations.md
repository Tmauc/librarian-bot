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

The chat destination uses the active client's file-size limit — see
[client limits](clients/README.md#per-platform-upload-limits). Email/Kindle are platform-neutral.

## The contract: `Destination`

Defined in [`librarian/destinations/base.py`](../librarian/destinations/base.py).

| Member | Purpose |
|---|---|
| `name` | Unique registry name (also the menu choice value). |
| `label` | Button label shown to the user. |
| `available(ctx) -> bool` | Whether it can be offered to this user now (default `True`). |
| `deliver(ctx, path, filename, title, caption)` | Send the file. Owns its status messages and error handling. |

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
Send to Kindle — use the `email` destination and drop the file into the Kobo's Dropbox/Google
Drive folder, or transfer over USB. A dedicated "Kobo" destination isn't needed (EPUB is native);
if you ever want automatic cloud drop-off, that's a natural [new destination](#adding-a-destination).
