"""Central configuration, read once from the environment.

Everything that reads ``os.environ`` for configuration lives here so sources and
clients depend on typed values, not on env-var names scattered across modules.
"""

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
        try:
            out.add(int(part))
        except ValueError:
            pass
    return out


# --- Shared / product-wide -------------------------------------------------
VERSION = "2.0.0"
GITHUB_REPO = os.environ.get("GITHUB_REPO", "owner/librarian-bot")
MAX_RESULTS = 10
MAX_QUERY_LENGTH = 200
RATE_LIMIT_SECONDS = 5
ALLOWED_FORMATS: list[str] = [
    f
    for f in (s.strip() for s in os.environ.get("ALLOWED_FORMATS", "epub,pdf").split(","))
    if f in _VALID_FORMATS
] or ["epub"]

# --- Sources ---------------------------------------------------------------
ANNA_ARCHIVE_URL = os.environ.get("ANNA_ARCHIVE_URL", "").rstrip("/")
PROWLARR_URL = os.environ.get("PROWLARR_URL", "").rstrip("/")
PROWLARR_API_KEY = os.environ.get("PROWLARR_API_KEY", "")
BOOKS_DOWNLOAD_PATH = os.environ.get("BOOKS_DOWNLOAD_PATH", "/downloads/books")
DOWNLOAD_TIMEOUT_MINUTES = int(os.environ.get("DOWNLOAD_TIMEOUT_MINUTES", "15"))

# --- Telegram client (platform-specific glue only) -------------------------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_ALLOWED_IDS: set[int] = _int_set(os.environ.get("ALLOWED_USER_IDS", ""))
LOCAL_API_SERVER = os.environ.get("LOCAL_API_SERVER", "").rstrip("/")
# Telegram caps uploads at 50 MB; a local Bot API server lifts it (we use 400 MB).
TELEGRAM_MAX_FILE_SIZE = 400 * 1024 * 1024 if LOCAL_API_SERVER else 50 * 1024 * 1024
