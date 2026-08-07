# Setup guide — Kobo + Discord + Google Drive

Goal: run the bot on **Discord**, and deliver books to a **Google Drive** folder that your
**Kobo** syncs. By the end your `.env` is filled and ready to test.

See also: [Discord client](../clients/discord.md) · [Destinations](../destinations.md) ·
[Configuration](../configuration.md).

## 0. Start the `.env`

```bash
cp .env.example .env
```

Open `.env`. For a Kobo, set the format to EPUB (native, uses the colour screen):

```
ALLOWED_FORMATS=epub
```

## 1. A source (so searches return something)

The bot needs at least one download source. The simplest is Anna's Archive — set the base URL
of the instance you use:

```
ANNA_ARCHIVE_URL=https://<your-anna-instance>
```

(Without a source, searches return "no results". Prowlarr is the alternative — see
[configuration](../configuration.md).)

## 2. Discord bot → `DISCORD_TOKEN`, `DISCORD_ALLOWED_IDS`

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) →
   **New Application** → name it → **Create**.
2. Left menu → **Bot**. Under **Privileged Gateway Intents**, turn on **MESSAGE CONTENT
   INTENT** → **Save Changes**. *(Required — the bot can't read your messages otherwise.)*
3. Still on **Bot** → **Reset Token** → **Copy**. That's your `DISCORD_TOKEN`.
4. Invite the bot: **OAuth2 → URL Generator**. Scope: **`bot`** only (not
   `applications.commands` — our commands are plain messages). Permissions: **View Channels**,
   **Send Messages**, **Read Message History**, **Attach Files**. Open the generated URL and add
   the bot to a server (you need a shared server before you can DM it).
5. Your user ID: Discord **Settings → Advanced → Developer Mode** (on), then right-click your
   name → **Copy User ID**. That's your `DISCORD_ALLOWED_IDS`.

```
DISCORD_TOKEN=paste-the-bot-token
DISCORD_ALLOWED_IDS=your-user-id
```

## 3. Google Drive → `GDRIVE_*`

### 3a. A Google Cloud project + Drive API

1. [Google Cloud Console](https://console.cloud.google.com/) → create (or pick) a project.
2. **APIs & Services → Library** → search **Google Drive API** → **Enable**.

### 3b. OAuth consent screen

1. **APIs & Services → OAuth consent screen** → User type **External** → fill the required
   names/email.
2. Add scope `.../auth/drive.file` (or just continue — the script requests it).
3. **Publishing status:** either add your Google account under **Test users**, or click
   **Publish app**. ⚠️ In *Testing* mode a refresh token **expires after 7 days**; **Publish**
   (In production) to get a durable token. `drive.file` only touches files the app creates, so
   an "unverified app" warning is fine — click **Advanced → Go to … (unsafe)** to proceed.

### 3c. OAuth client → client id/secret

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**.
2. Application type: **Desktop app** → **Create**. Copy the **Client ID** and **Client secret**.

### 3d. Get the refresh token (helper script)

From the repo root, with the venv active:

```bash
python scripts/get_gdrive_token.py
```

Paste the Client ID and secret when asked. A browser opens → authorize with the Google account
whose Drive you'll use. (Run this on a machine **with a browser** — e.g. your Mac. If the bot
will live on a headless NUC/Raspberry Pi, generate the token here and just paste the `GDRIVE_*`
lines into the `.env` you deploy.) The script prints:

```
GDRIVE_CLIENT_ID=...
GDRIVE_CLIENT_SECRET=...
GDRIVE_REFRESH_TOKEN=...
```

Paste those three lines into `.env`.

### 3e. The destination folder → `GDRIVE_FOLDER_ID`

1. In [Google Drive](https://drive.google.com), create a folder (e.g. **Kobo**).
2. Open it; the URL ends with the folder id: `…/folders/`**`THIS_PART`**.

```
GDRIVE_FOLDER_ID=the-part-after-/folders/
```

(Leave empty to drop files in your Drive root.)

## 4. Point the Kobo at that folder

On the Kobo: **Home → More (≡) → Google Drive** → sign in with the **same** Google account →
grant access. Books the bot uploads to the folder then appear after a sync.

## 5. Final `.env` (the lines that matter)

```
ALLOWED_FORMATS=epub
ANNA_ARCHIVE_URL=https://<your-anna-instance>

DISCORD_TOKEN=...
DISCORD_ALLOWED_IDS=...

GDRIVE_CLIENT_ID=...
GDRIVE_CLIENT_SECRET=...
GDRIVE_REFRESH_TOKEN=...
GDRIVE_FOLDER_ID=...
```

Leave `TELEGRAM_TOKEN` empty (Discord-only is fine). `.env` is gitignored — it stays local.

## 6. Try it

```bash
python main.py
```

You should see `Discord connecté en tant que …` and `Bot started.`. In Discord, DM the bot
`/start`, then send a book title → pick a result → choose **☁️ Google Drive** → the book lands
in the folder and syncs to your Kobo.

> Google OAuth feeling fiddly? **Dropbox** is simpler (refresh tokens don't expire) — same idea,
> see [Destinations](../destinations.md).
