"""Deliver to a Kindle via Amazon's Send to Kindle (SMTP with the 'convert' subject)."""

from librarian.destinations.base import MailDestination


class KindleDestination(MailDestination):
    name = "kindle"
    label = "📖 Kindle"
    pref_key = "kindle_email"
    kindle = True
    channel = "Kindle"
