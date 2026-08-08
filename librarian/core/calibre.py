"""Locate Calibre's CLI tools (``ebook-convert``, ``ebook-meta``).

Calibre ships these binaries inside its install/app bundle, which is frequently
NOT on ``PATH`` — the macOS ``Calibre.app`` is the common case: the tools live in
``/Applications/calibre.app/Contents/MacOS`` but ``shutil.which`` finds nothing,
so the bot wrongly reports Calibre as absent. We check ``PATH`` first, then a few
well-known locations, plus an optional ``CALIBRE_BIN_DIR`` override.

Shared by ``conversion`` (MOBI/AZW3) and ``metadata`` (rewriting the OPF).
"""

from __future__ import annotations

import os
import shutil
import sys

from librarian import config

# Well-known install locations, checked after PATH and the configured override.
_KNOWN_DIRS = (
    "/Applications/calibre.app/Contents/MacOS",   # macOS app bundle
    os.path.expanduser("~/Applications/calibre.app/Contents/MacOS"),
    "/opt/homebrew/bin",                           # Apple-silicon Homebrew
    "/usr/local/bin",                              # Intel Homebrew / manual
    "/usr/bin",                                    # Linux packages
    r"C:\Program Files\Calibre2",                  # Windows
    r"C:\Program Files (x86)\Calibre2",
)


def _candidate_dirs() -> list[str]:
    dirs = [config.CALIBRE_BIN_DIR] if config.CALIBRE_BIN_DIR else []
    dirs.extend(_KNOWN_DIRS)
    return dirs


def tool(name: str) -> str | None:
    """Absolute path to a Calibre CLI tool (e.g. ``ebook-meta``), or None."""
    found = shutil.which(name)
    if found:
        return found
    exe = name + (".exe" if sys.platform == "win32" else "")
    for d in _candidate_dirs():
        path = os.path.join(d, exe)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def available() -> bool:
    """True if Calibre's conversion/metadata tools can be found."""
    return tool("ebook-convert") is not None
