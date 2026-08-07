# Clients (messaging adapters)

A **client** is a thin adapter that lets users talk to the bot from a messaging
platform. All conversation logic lives once in [`librarian/clients/flow.py`](../../librarian/clients/flow.py);
an adapter only renders that flow onto its platform and routes events back.

Available clients:

- [Telegram](telegram.md)
- [Discord](discord.md)

See also: [architecture](../architecture.md) · [configuration](../configuration.md).

## The contract: `ClientContext`

Defined in [`librarian/clients/base.py`](../../librarian/clients/base.py). The flow calls
these; the adapter implements the four primitives (the rest is provided by the base).

| Member | Kind | Purpose |
|---|---|---|
| `max_file_size` | property | Platform upload limit in bytes. |
| `_send(text, choices)` | abstract | Send a new message (optionally with choice buttons); return a handle. |
| `_edit(handle, text, choices)` | abstract | Edit that message in place. |
| `_send_document(path, filename, caption)` | abstract | Upload a file. |
| `ask_choice` / `ask_text` / `say` / `update_status` / `send_document` | provided | Flow-facing API built on the primitives. |

`ask_choice` / `ask_text` park an `asyncio.Future` on the user's `Session`; the flow
suspends until the adapter resolves it.

## Routing: the `Session`

The adapter maps incoming platform events onto the per-user `Session`:

| Event | Adapter calls |
|---|---|
| Text message (a pending prompt) | `session.resolve_text(text)` |
| Text message (otherwise) | start a flow: `run_search` / `run_start` / `run_settings` |
| Button/component click | `session.resolve_choice(value)` |
| The reserved `__cancel__` value | `session.cancel()` (cancels the flow task) |

`Choice.value` is short, so it can double as the platform's callback/custom id.

## <a name="adding-a-client"></a>Adding a client

1. Create `librarian/clients/<platform>/adapter.py` with:
   - a `ClientContext` subclass implementing `max_file_size`, `_send`, `_edit`, `_send_document`;
   - a client class holding `dict[user_id, Session]`, a whitelist check, and event handlers
     that route into the session (see the table above). Start flows with a small runner that
     swallows `CancelledError`.
2. Add a whitelist + token to [`librarian/config.py`](../../librarian/config.py) and
   [`.env.example`](../../.env.example).
3. Start it in [`main.py`](../../main.py) alongside the others.
4. Add a doc page here and link it from [the index](../README.md) and this list.
5. Add adapter tests (mapping, whitelist, routing) — see `tests/test_telegram_adapter.py`.

Nothing in `core/` or `clients/flow.py` changes. Reference implementations: Telegram and
Discord adapters, ~120 lines each.

## Per-platform upload limits

| Platform | Limit | Set in |
|---|---|---|
| Telegram | 50 MB (400 MB with a local Bot API server) | `config.TELEGRAM_MAX_FILE_SIZE` |
| Discord | 25 MB | `config.DISCORD_MAX_FILE_SIZE` |

Oversized results are skipped automatically (the flow auto-retries the next result).
