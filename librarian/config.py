"""Central configuration, read once from the environment.

Everything that reads ``os.environ`` for configuration lives here so sources and
clients depend on typed values, not on env-var names scattered across modules.
"""

import contextlib
import os

from dotenv import load_dotenv

load_dotenv()  # load .env before anything reads these values

_VALID_FORMATS = {"epub", "pdf", "mobi", "azw3"}


def _int_set(raw: str) -> set[int]:
    out: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        with contextlib.suppress(ValueError):
            out.add(int(part))
    return out


# --- Shared / product-wide -------------------------------------------------
VERSION = "2.5.0"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "owner/librarian-bot")
MAX_RESULTS = 25  # cap on results shown (also the max options in a Discord select menu)
MAX_QUERY_LENGTH = 200
RATE_LIMIT_SECONDS = 5
ALLOWED_FORMATS: list[str] = [
    f
    for f in (s.strip() for s in os.environ.get("ALLOWED_FORMATS", "epub,pdf").split(","))
    if f in _VALID_FORMATS
] or ["epub"]

# --- Intelligence (optional local LLM, e.g. Ollama) ------------------------
# LLM_MODEL empty = disabled (the bot does a plain single search). A small local
# model works; ~3B (qwen2.5:3b / llama3.2:3b) is recommended for series enumeration.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "")

# --- Sources ---------------------------------------------------------------
ANNA_ARCHIVE_URL = os.environ.get("ANNA_ARCHIVE_URL", "").rstrip("/")
PROWLARR_URL = os.environ.get("PROWLARR_URL", "").rstrip("/")
PROWLARR_API_KEY = os.environ.get("PROWLARR_API_KEY", "")
BOOKS_DOWNLOAD_PATH = os.environ.get("BOOKS_DOWNLOAD_PATH", "/downloads/books")
DOWNLOAD_TIMEOUT_MINUTES = int(os.environ.get("DOWNLOAD_TIMEOUT_MINUTES", "15"))

# --- Cloud destinations (optional; single-account, e.g. to feed a Kobo) ----
# Dropbox: one app (key/secret) + a long-lived refresh token + a target folder.
DROPBOX_APP_KEY = os.environ.get("DROPBOX_APP_KEY", "")
DROPBOX_APP_SECRET = os.environ.get("DROPBOX_APP_SECRET", "")
DROPBOX_REFRESH_TOKEN = os.environ.get("DROPBOX_REFRESH_TOKEN", "")
DROPBOX_FOLDER = os.environ.get("DROPBOX_FOLDER", "/librarian-bot")
# Google Drive: OAuth client (id/secret) + refresh token + destination folder id.
GDRIVE_CLIENT_ID = os.environ.get("GDRIVE_CLIENT_ID", "")
GDRIVE_CLIENT_SECRET = os.environ.get("GDRIVE_CLIENT_SECRET", "")
GDRIVE_REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "")

# --- Telegram client (platform-specific glue only) -------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_ALLOWED_IDS: set[int] = _int_set(os.environ.get("ALLOWED_USER_IDS", ""))
LOCAL_API_SERVER = os.environ.get("LOCAL_API_SERVER", "").rstrip("/")
# Telegram caps uploads at 50 MB; a local Bot API server lifts it (we use 400 MB).
TELEGRAM_MAX_FILE_SIZE = 400 * 1024 * 1024 if LOCAL_API_SERVER else 50 * 1024 * 1024

# --- Discord client (platform-specific glue only) --------------------------
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_ALLOWED_IDS: set[int] = _int_set(os.environ.get("DISCORD_ALLOWED_IDS", ""))
# Discord's upload limit for non-boosted servers/DMs is 25 MB.
DISCORD_MAX_FILE_SIZE = 25 * 1024 * 1024
