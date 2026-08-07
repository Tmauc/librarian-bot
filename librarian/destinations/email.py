"""Deliver by email (plain SMTP attachment to the user's stored address)."""

from librarian.destinations.base import MailDestination


class EmailDestination(MailDestination):
    name = "email"
    label = "📧 Email"
    pref_key = "email"
    kindle = False
    channel = "email"
