# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the bot

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
python main.py

# Tests
pip install -r requirements-dev.txt
python -m pytest

# Run via Docker
docker compose up -d --build
docker compose logs -f bot
```

The bot process is long-running. When working locally during development, kill the background process before restarting: use `TaskStop` on the running task ID.

## Architecture

> Detailed docs live in [`docs/`](docs/README.md) — one page per client adapter, per
> source, plus architecture/configuration/delivery/development. Update them when you add
> or change an adapter or source.

Ports & adapters (hexagonal). The core knows nothing about any messaging platform
or any download provider — it talks only to interfaces. Package layout:

```
librarian/
  config.py            # all env reading, once, into typed values
  core/
    models.py          # SearchResult (source-neutral; ref={} holds opaque source data)
    search_service.py  # fan out to enabled sources, merge/order/dedup
    download_service.py# dispatch a result back to the source that produced it
    conversion.py delivery.py scanning.py prefs.py watcher.py netfetch.py security.py
  sources/
    base.py            # Source ABC: search() / download()
    registry.py        # THE place to register a source (a list)
    anna.py prowlarr.py
  clients/
    base.py            # ClientContext port + Session (resumable, future-based)
    flow.py            # the ENTIRE conversation UX, platform-agnostic
    telegram/adapter.py# Telegram-specific code (+ create_application, update job)
    discord/adapter.py # Discord-specific code (discord.py Views/interactions)
main.py                # starts every configured client (Telegram and/or Discord)
```

`main.py` runs all configured clients concurrently in one asyncio loop: it starts
the Telegram `Application` (programmatic initialize/start/start_polling) and/or
`DiscordClient.start()`. Configure at least one of `TELEGRAM_TOKEN` / `DISCORD_TOKEN`.

### Two extension axes (the whole point)
- **Add a download source**: create `librarian/sources/<name>.py` with a `Source`
  subclass, add it to `registry._ALL`. No core/client change.
- **Add a client platform** (Discord is the reference impl; WhatsApp next): create
  an adapter under `librarian/clients/` implementing `ClientContext` (`_send`/
  `_edit`/`_send_document`/`max_file_size`) and routing incoming events into the
  `Session` (`resolve_text`/`resolve_choice`/`cancel`); start it in `main.py`.
  No core/flow change. Telegram and Discord adapters are ~120 lines each.

### Conversation model
`flow.py` is a set of linear coroutines (`run_start`, `run_settings`,
`run_search`). It awaits `ctx.ask_choice()` / `ctx.ask_text()`, which park an
`asyncio.Future` on the user's `Session`. The adapter resolves that future when a
button/message arrives — this future bridge is the only per-platform plumbing.
Cancellation = the adapter calls `session.cancel()`, cancelling the flow task; the
download helper cleans partial temp files on `CancelledError` (a `BaseException`).

### Search / download flow
`search_service.search()` runs every enabled source's `search()` in parallel,
orders (e-reader formats first, direct before torrents), drops oversized, dedups by
full normalized title. A pick goes through `_deliver` in flow: `download_service.fetch()`
(auto-retry across results, size guard) → convert (EPUB→PDF via PyMuPDF
`convert_to_pdf`; MOBI/AZW3 require Calibre) → VirusTotal scan → deliver (this chat
via `send_document`, or email/Kindle via `delivery`).

## Key constraints

- **Per-platform upload limits** live on the adapter (`ctx.max_file_size`).
  Telegram is 50 MB (400 MB with a local Bot API server via `LOCAL_API_SERVER` +
  `LOCAL_API_ID`/`LOCAL_API_HASH`). Discord ~25 MB, WhatsApp ~100 MB when added.
- **Anna's Archive JSON API returns 404** — the source scrapes the HTML search page.
- **Mirror resolution is slow** — libgen.is is blocked in France; `libgen.li/ads.php`
  returns an intermediate HTML page scraped for the real `get.php?key=` URL.
- **SSRF guard** (`core/security._is_safe_url`) resolves hostnames and rejects any
  that map to internal IPs. Residual DNS-rebinding TOCTOU is documented in the code.
- **Whitelist** is per-platform, owned by the adapter (`config.TELEGRAM_ALLOWED_IDS`).
- **Prefs keys are namespaced strings** (`telegram:123`) so platforms don't collide.

## Environment variables

See `.env.example`. Required: `TELEGRAM_TOKEN`, `ALLOWED_USER_IDS`. Optional but
needed for torrents: `PROWLARR_URL`, `PROWLARR_API_KEY`, `BOOKS_DOWNLOAD_PATH`.
