# Telegram client

Adapter: [`librarian/clients/telegram/adapter.py`](../../librarian/clients/telegram/adapter.py).
See the [client contract](README.md) and [configuration](../configuration.md).

## Setup

1. In Telegram, open **@BotFather** → `/newbot` → follow the prompts. Copy the **token**.
2. Find your numeric ID via **@userinfobot** (`/start` → `Id:`).
3. In `.env`:
   ```
   TELEGRAM_TOKEN=123456789:AA...
   ALLOWED_USER_IDS=123456789        # comma-separated for several users
   ```
4. Run the bot and send `/start`.

A full beginner walkthrough is in [`LISEZMOI.md`](../../LISEZMOI.md) (FR) and the
[English README](../../README.md).

## Commands

- `/start` — greeting, or first-run onboarding (format, email, Kindle).
- `/settings` — change format / email / Kindle address, or delete your data.
- Any other text — a book search.

## Config

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | BotFather token (empty = disabled). |
| `ALLOWED_USER_IDS` | Whitelisted numeric user IDs. |
| `LOCAL_API_SERVER` | Local Bot API URL — raises the upload limit from 50 MB to 400 MB. |
| `LOCAL_API_ID` / `LOCAL_API_HASH` | From [my.telegram.org](https://my.telegram.org), for the local server. |

## Platform specifics

- **Upload limit: 50 MB** by default. To send larger files, run a local
  [`telegram-bot-api`](https://github.com/tdlib/telegram-bot-api) server and set
  `LOCAL_API_SERVER=http://telegram-bot-api:8081` (see [`docker-compose.yml`](../../docker-compose.yml)).
- UI uses **inline keyboards**; the interaction message is **edited in place** as the flow
  progresses. Stale-button taps (on an old message) are ignored.
- Runs with `concurrent_updates(True)` so a long download never blocks other users or the
  Cancel button.
- `create_application()` wires handlers, a global error handler, and a daily update-check job.
