# Prowlarr source

Implementation: [`librarian/sources/prowlarr.py`](../../librarian/sources/prowlarr.py).
See the [source contract](README.md) and [configuration](../configuration.md).

## Config

| Variable | Description |
|---|---|
| `PROWLARR_URL` | Prowlarr base URL, e.g. `http://localhost:9696`. Empty = source disabled. |
| `PROWLARR_API_KEY` | API key (Prowlarr → Settings → General → API Key). |
| `BOOKS_DOWNLOAD_PATH` | Folder your torrent client writes completed downloads to. |
| `DOWNLOAD_TIMEOUT_MINUTES` | How long to wait for a torrent to finish (default `15`). |

Enabled when `PROWLARR_URL` is set. Searches book categories (`7000`, `7020`).

## How it works

The source fully owns both download paths:

- **Direct** (`is_torrent = False`): streams `ref["download_url"]` to a temp file (SSRF-checked,
  content-type validated).
- **Torrent** (`is_torrent = True`): calls Prowlarr's `/api/v1/download` to hand the release to
  your download client, then [`core/watcher.py`](../../librarian/core/watcher.py) polls
  `BOOKS_DOWNLOAD_PATH` until a matching book file appears (fuzzy title match), up to
  `DOWNLOAD_TIMEOUT_MINUTES`.

`ref` carries `{"guid", "indexer_id", "download_url", "magnet_url"}`.

## Notes

- The watcher matches by significant-word overlap between the release title and the file name,
  so the download client must save into `BOOKS_DOWNLOAD_PATH`.
- Torrent files are owned by the download client (not temp files) and are **not** deleted by the
  bot after sending.
