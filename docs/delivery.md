# Delivery (where the file ends up)

After a book is downloaded, [converted](architecture.md) and scanned, the flow delivers it to
one of three destinations. The user picks when they have an email or Kindle address configured;
otherwise it goes to the chat.

| Destination | How | Implementation |
|---|---|---|
| **This chat** (`here`) | The active client uploads the file | `ClientContext.send_document` ([clients](clients/README.md)) |
| **Email** | SMTP attachment | [`core/delivery.py`](../librarian/core/delivery.py) |
| **Kindle** | SMTP with subject `convert` (Send to Kindle) | [`core/delivery.py`](../librarian/core/delivery.py) |

The chat destination is platform-specific (its file-size limit is the client's — see
[client limits](clients/README.md#per-platform-upload-limits)). Email/Kindle are platform-neutral.

## Email / Send to Kindle setup (SMTP)

SMTP is configured globally in `.env`; each user sets their own destination address via
`/settings`.

| Variable | Description |
|---|---|
| `SMTP_HOST` / `SMTP_PORT` | Server (default `smtp.gmail.com` / `587`, STARTTLS). |
| `SMTP_USER` / `SMTP_PASSWORD` | Login. For Gmail, generate an **App Password** at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). |
| `SMTP_FROM` | Sender address (defaults to `SMTP_USER`). |

**Send to Kindle:** add the `SMTP_FROM` address to your Amazon account's *Approved Personal
Document E-mail List* (Manage Your Content and Devices → Preferences → Personal Document
Settings). The user's Kindle address (e.g. `you@kindle.com`) is stored per-user via `/settings`.
Amazon converts the file on its side, so [Calibre](../README.md) is not required for Kindle.

## VirusTotal scan (optional)

If `VIRUSTOTAL_API_KEY` is set, the file is scanned before sending
([`core/scanning.py`](../librarian/core/scanning.py)): checked by SHA-256 first (no upload if
already known), else uploaded and polled. **Malicious** files are blocked; **suspicious** files
are sent with a caption warning. Files over 32 MB are skipped.

## Format conversion

The delivered format depends on the chosen format and the source file — see
[conversion in the architecture overview](architecture.md). EPUB→PDF works out of the box;
MOBI/AZW3 require Calibre and otherwise fall back to sending the EPUB.
