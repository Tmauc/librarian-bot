"""Persistent user preferences storage (JSON-based, per-user).

Keys are opaque strings so different client platforms can namespace their users
without colliding, e.g. ``"telegram:123"`` vs ``"discord:456"``.
"""

import asyncio
import json
import os
import tempfile
from typing import Any

# Default beside the repo root (…/librarian/core/prefs.py → repo root), matching
# the historical location; override with USER_PREFS_FILE (used in Docker).
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_default_prefs_file = os.path.join(_repo_root, "user_prefs.json")
PREFS_FILE = os.environ.get("USER_PREFS_FILE") or _default_prefs_file
_lock = asyncio.Lock()


async def get(user_key: str) -> dict:
    """Get all preferences for a user. Returns {} if user not found."""
    async with _lock:
        if not os.path.exists(PREFS_FILE):
            return {}
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(str(user_key), {})
        except Exception:
            return {}


async def get_all(user_key: str) -> dict:
    """Alias for get()."""
    return await get(user_key)


async def set(user_key: str, key: str, value: Any) -> None:
    """Set a preference key for a user. Atomic write via temp file + os.replace."""
    async with _lock:
        data = {}
        if os.path.exists(PREFS_FILE):
            try:
                with open(PREFS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        uk = str(user_key)
        if uk not in data:
            data[uk] = {}
        data[uk][key] = value

        _atomic_write(data)


async def delete_user(user_key: str) -> None:
    """Delete all preferences for a user."""
    async with _lock:
        if not os.path.exists(PREFS_FILE):
            return
        try:
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        uk = str(user_key)
        if uk in data:
            del data[uk]
            _atomic_write(data)


def _atomic_write(data: dict) -> None:
    # Temp file must be on the same filesystem as the destination (os.replace).
    dest_dir = os.path.dirname(os.path.abspath(PREFS_FILE))
    os.makedirs(dest_dir, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(text=True, suffix=".json", dir=dest_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, PREFS_FILE)
    except Exception:
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        raise
