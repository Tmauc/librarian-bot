"""EPUB→PDF/MOBI/AZW3 conversion (moved from converter.py)."""

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import tempfile

import fitz  # pymupdf

logger = logging.getLogger(__name__)


def ebook_convert_available() -> bool:
    """Return True if Calibre's ebook-convert is available in PATH."""
    return shutil.which("ebook-convert") is not None


def _convert_sync(epub_path: str) -> str:
    """Blocking epub→PDF conversion using PyMuPDF.

    NOTE: ``Document.save()`` only works on documents that are already PDFs — on
    an EPUB it raises ``AssertionError``. Real conversion goes through
    ``convert_to_pdf()``, which renders the source into PDF bytes we then reopen
    and save.
    """
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf", prefix="librarian_") as f:
        pdf_path = f.name
    try:
        src = fitz.open(epub_path)
        try:
            pdf_bytes = src.convert_to_pdf()
        finally:
            src.close()
        pdf = fitz.open("pdf", pdf_bytes)
        try:
            pdf.save(pdf_path)
        finally:
            pdf.close()
        if not os.path.exists(pdf_path) or os.path.getsize(pdf_path) < 1024:
            raise RuntimeError("PyMuPDF produced an empty or missing PDF")
        return pdf_path
    except Exception:
        with contextlib.suppress(Exception):
            os.remove(pdf_path)
        raise


async def epub_to_pdf(epub_path: str) -> str:
    """Convert an epub file to PDF. Returns path to PDF temp file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _convert_sync, epub_path)


def _convert_to_format_sync(epub_path: str, fmt: str) -> str:
    """Blocking epub→MOBI/AZW3 conversion. Requires Calibre's ebook-convert.

    PyMuPDF cannot produce MOBI/AZW3 at all (``save()`` only emits PDF bytes, and
    only for PDF documents), so there is no meaningful fallback: emitting a
    PDF-under-a-.mobi-name yields a file Kindle rejects. We raise instead, and the
    caller degrades gracefully by sending the original EPUB.
    """
    if not ebook_convert_available():
        raise RuntimeError(
            f"{fmt.upper()} conversion requires Calibre (ebook-convert). "
            "Install Calibre, or choose EPUB/PDF instead."
        )

    suffix = f".{fmt}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="librarian_") as f:
        output_path = f.name

    try:
        _convert_with_calibre(epub_path, output_path)
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            raise RuntimeError(f"Conversion produced an empty or missing {fmt.upper()} file")
        return output_path
    except Exception:
        with contextlib.suppress(Exception):
            os.remove(output_path)
        raise


def _convert_with_calibre(input_path: str, output_path: str) -> None:
    """Convert using Calibre's ebook-convert CLI (blocking)."""
    cmd = shutil.which("ebook-convert")
    result = subprocess.run(
        [cmd, input_path, output_path],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ebook-convert failed: {result.stderr[:300]}")


async def epub_to_mobi(epub_path: str) -> str:
    """Convert an epub file to MOBI. Returns path to MOBI temp file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _convert_to_format_sync, epub_path, "mobi")


async def epub_to_azw3(epub_path: str) -> str:
    """Convert an epub file to AZW3. Returns path to AZW3 temp file."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _convert_to_format_sync, epub_path, "azw3")
