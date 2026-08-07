"""librarian — a platform-agnostic ebook search/download bot.

Layout:
- ``librarian.core``    : platform-neutral domain logic and services
- ``librarian.sources`` : pluggable download providers (add one = one file)
- ``librarian.clients`` : messaging front-ends; only the adapter is platform-specific
"""

__version__ = "2.2.0"
