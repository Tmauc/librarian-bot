"""SMTP email sending (generic email + Send to Kindle). Moved from mailer.py."""

import asyncio
import logging
import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM") or SMTP_USER


def is_configured() -> bool:
    """Check if SMTP is properly configured."""
    return bool(SMTP_USER and SMTP_PASSWORD)


def _send_sync(file_path: str, filename: str, recipient_email: str, subject: str = "") -> None:
    """Send a file via SMTP (synchronous, blocking)."""
    if not is_configured():
        raise RuntimeError("SMTP not configured (SMTP_USER and SMTP_PASSWORD required)")

    if not subject:
        subject = filename

    with open(file_path, "rb") as f:
        file_data = f.read()

    msg = MIMEMultipart()
    msg["From"] = SMTP_FROM
    msg["To"] = recipient_email
    msg["Subject"] = subject

    body = MIMEText("📖 Sent by librarian-bot", "plain")
    msg.attach(body)

    attachment = MIMEApplication(file_data, name=filename)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

    logger.info(f"Email sent to {recipient_email}: {filename}")


async def send_file(file_path: str, filename: str, recipient_email: str, kindle: bool = False) -> None:
    """Send a file via email (async wrapper around blocking send).

    If ``kindle`` is True the subject is set to ``convert`` for Send to Kindle.
    """
    subject = "convert" if kindle else ""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_sync, file_path, filename, recipient_email, subject)
