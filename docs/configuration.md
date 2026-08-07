# Configuration reference

All configuration is read once from the environment in [`librarian/config.py`](../librarian/config.py).
Copy [`.env.example`](../.env.example) to `.env` and fill in what you need. **Configure at
least one client** (Telegram and/or Discord).

## Clients

| Variable | Used by | Description |
|---|---|---|
| `TELEGRAM_TOKEN` | [Telegram](clients/telegram.md) | BotFather token. Empty = Telegram disabled. |
| `ALLOWED_USER_IDS` | Telegram | Comma-separated numeric Telegram user IDs (whitelist). |
| `LOCAL_API_SERVER` | Telegram | Local Bot API URL to lift the 50 MB upload limit (→ 400 MB). |
| `LOCAL_API_ID` / `LOCAL_API_HASH` | Telegram | Credentials from [my.telegram.org](https://my.telegram.org), for the local Bot API server. |
| `DISCORD_TOKEN` | [Discord](clients/discord.md) | Bot token. Empty = Discord disabled. |
| `DISCORD_ALLOWED_IDS` | Discord | Comma-separated numeric Discord user IDs (whitelist). |

## Sources

| Variable | Used by | Description |
|---|---|---|
| `ANNA_ARCHIVE_URL` | [Anna's Archive](sources/anna-archive.md) | Base URL of the instance. Empty = disabled. |
| `PROWLARR_URL` | [Prowlarr](sources/prowlarr.md) | Prowlarr base URL, e.g. `http://localhost:9696`. Empty = disabled. |
| `PROWLARR_API_KEY` | Prowlarr | API key (Prowlarr → Settings → General). |
| `BOOKS_DOWNLOAD_PATH` | Prowlarr | Folder the torrent client writes completed downloads to. |
| `DOWNLOAD_TIMEOUT_MINUTES` | Prowlarr | How long to wait for a torrent (default `15`). |

## Formats & delivery

| Variable | Used by | Description |
|---|---|---|
| `ALLOWED_FORMATS` | [flow](architecture.md) | Offered formats among `epub,pdf,mobi,azw3` (default `epub,pdf`). |
| `SMTP_HOST` / `SMTP_PORT` | [destinations](destinations.md) | SMTP server (default `smtp.gmail.com` / `587`). |
| `SMTP_USER` / `SMTP_PASSWORD` | delivery | SMTP login. Gmail: use an App Password. |
| `SMTP_FROM` | delivery | Sender address (defaults to `SMTP_USER`). |
| `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET` / `DROPBOX_REFRESH_TOKEN` / `DROPBOX_FOLDER` | [destinations](destinations.md) | Dropbox cloud destination (single account). Empty = disabled. |
| `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` / `GDRIVE_REFRESH_TOKEN` / `GDRIVE_FOLDER_ID` | [destinations](destinations.md) | Google Drive cloud destination (single account). Empty = disabled. |

## Other

| Variable | Description |
|---|---|
| `VIRUSTOTAL_API_KEY` | Scan files before sending ([destinations](destinations.md)). Empty = disabled. |
| `GITHUB_REPO` | `owner/repo` watched for new releases (Telegram notification). Empty = disabled. |
| `USER_PREFS_FILE` | Path to the per-user prefs JSON. Defaults next to the repo root; set in Docker. |

Preference keys are namespaced per platform (`telegram:123`, `discord:456`) so users on
different platforms never collide.
