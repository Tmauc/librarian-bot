# Discord client

Adapter: [`librarian/clients/discord/adapter.py`](../../librarian/clients/discord/adapter.py).
Built on [discord.py](https://discordpy.readthedocs.io/). See the [client contract](README.md)
and [configuration](../configuration.md).

## Setup

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) →
   **New Application** → name it → **Create**.
2. Left menu → **Bot**. Under **Privileged Gateway Intents**, enable **MESSAGE CONTENT
   INTENT** (required to read messages) → **Save Changes**.
3. Still under **Bot**: **Reset Token** → **Copy** the token.
4. Invite the bot: **OAuth2 → URL Generator**. Scope: **`bot`** only (the bot's commands are
   plain messages, not slash commands — no `applications.commands`). Permissions: **View
   Channels**, **Send Messages**, **Read Message History**, **Attach Files**. Open the generated
   URL and add the bot to a server (you need a shared server to DM the bot too).
5. Get your user ID: Discord **Settings → Advanced → Developer Mode**, then right-click your
   name → **Copy User ID**.
6. In `.env`:
   ```
   DISCORD_TOKEN=your-bot-token
   DISCORD_ALLOWED_IDS=your-user-id      # comma-separated for several users
   ```
7. Run the bot and send `/start` to it.

## Commands

Same as Telegram: `/start`, `/settings`, or any text to search. `!start` / `!settings`
also work.

## Config

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Bot token (empty = disabled). |
| `DISCORD_ALLOWED_IDS` | Whitelisted numeric user IDs. |

## Platform specifics

- **Upload limit: 25 MB** (`config.DISCORD_MAX_FILE_SIZE`). Larger results are skipped
  automatically.
- Choices render as **button Views** (`discord.ui.View`); up to 5 buttons per row, laid out
  across rows. `discord.ui.View` must be built inside a running event loop.
- Button clicks are acknowledged with `interaction.response.defer()` within 3 s; the flow then
  edits the message. Clicks on stale messages are ignored.
- Requires the **Message Content intent** (privileged) — the bot cannot read search queries
  without it.
- Sessions are namespaced `discord:<id>` so they never collide with Telegram users.
- TLS: discord.py (aiohttp) uses the default SSL context, which on the macOS python.org build
  can lack CA certs ("certificate verify failed"). `main.py` points `SSL_CERT_FILE` at the
  bundled `certifi` before importing the adapter, so this works out of the box.
