# Anna's Archive source

Implementation: [`librarian/sources/anna.py`](../../librarian/sources/anna.py).
See the [source contract](README.md) and [configuration](../configuration.md).

## Config

| Variable | Description |
|---|---|
| `ANNA_ARCHIVE_URL` | Base URL of the instance to use. Empty = source disabled. |

Enabled when `ANNA_ARCHIVE_URL` is set. HTTPS is recommended (an HTTP URL logs a warning at
startup).

## How it works

- **Search** scrapes the HTML search page (`/search`). The JSON API is not used — it returns
  404. Results are keyed by md5; the `ref` carries `{"md5": ...}`.
- **Download** scrapes the book page (`/md5/<md5>`) for mirror links, then streams the first
  working one. Some mirrors (e.g. `libgen.li/ads.php`) return an intermediate HTML page that is
  scraped for the real `get.php?...` file link.
- **SSRF safety**: candidate URLs go through [`security._is_safe_url`](../../librarian/core/security.py);
  redirects are checked by an httpx hook. URLs under the admin-configured `ANNA_ARCHIVE_URL` are
  trusted; `.onion` links are skipped.
- Intermediate HTML pages are size-capped (5 MB) to avoid reading a huge body.

## Notes

- libgen.is is blocked in some countries (e.g. France); the bot relies on the other mirrors.
- If downloads fail with "all sources unavailable", it is often DNS — switch to `1.1.1.1` /
  `8.8.8.8` (see the [README troubleshooting](../../README.md#troubleshooting)).
