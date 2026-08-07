# Architecture

Ports & adapters (hexagonal). The core is entirely platform- and source-agnostic; it
talks only to interfaces. Two independent extension axes — [clients](clients/README.md)
and [sources](sources/README.md) — plug into it without any core change.

## Package layout

```
librarian/
  config.py            # all env reading, once, into typed values → docs/configuration.md
  core/
    models.py          # SearchResult (source-neutral; ref={} holds opaque source data)
    search_service.py  # fan out to enabled sources, merge/order/dedup
    download_service.py# dispatch a result back to the source that produced it
    conversion.py      # EPUB→PDF/MOBI/AZW3
    delivery.py        # email / Send to Kindle          → docs/delivery.md
    scanning.py        # VirusTotal
    prefs.py netfetch.py security.py watcher.py
  sources/
    base.py            # Source contract                 → docs/sources/README.md
    registry.py        # the one place to register a source
    anna.py prowlarr.py
  clients/
    base.py            # ClientContext port + Session     → docs/clients/README.md
    flow.py            # the ENTIRE conversation UX, platform-agnostic
    telegram/adapter.py# Telegram-specific code           → docs/clients/telegram.md
    discord/adapter.py # Discord-specific code            → docs/clients/discord.md
main.py                # starts every configured client concurrently
```

## The two seams

| Seam | Contract | Registered / started in | Guide |
|---|---|---|---|
| **Source** (download provider) | `Source.search()` / `download()` | `sources/registry.py` | [Add a source](sources/README.md#adding-a-source) |
| **Client** (messaging platform) | `ClientContext` + `Session` routing | `main.py` | [Add a client](clients/README.md#adding-a-client) |

Neither seam requires touching `core/` or `clients/flow.py`.

## Request lifecycle

```
user message ──▶ client adapter ──▶ flow.run_search(ctx, query)
                                        │
                                        ├─ search_service.search()  ── fan out to every
                                        │                              enabled Source, merge,
                                        │                              order (epub first), dedup
                                        ├─ ctx.ask_choice(...)       ── pick / format / destination
                                        ├─ download_service.fetch()  ── dispatch to the owning Source
                                        ├─ conversion (if needed)    ── EPUB→PDF/MOBI/AZW3
                                        ├─ scanning (VirusTotal)
                                        └─ delivery                  ── ctx.send_document (this chat)
                                                                        or email / Kindle
```

Only the **client adapter** box is platform-specific. Everything else is shared.

## Conversation model (Session + resumable flow)

`clients/flow.py` is a set of linear coroutines (`run_start`, `run_settings`,
`run_search`). They `await ctx.ask_choice()` / `ctx.ask_text()`, which park an
`asyncio.Future` on the user's `Session`. The adapter resolves that future when a
button/message arrives (`Session.resolve_choice` / `resolve_text`). Cancellation =
the adapter calls `Session.cancel()`, which cancels the flow task; the download helper
cleans partial temp files on `CancelledError`.

This future bridge is the **only** per-platform plumbing — see
[Client adapters](clients/README.md).

## Related

- [Configuration reference](configuration.md)
- [Development](development.md)
