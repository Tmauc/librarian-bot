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

## Troubleshooting: every download fails ("All mirrors failed")

The usual cause is a **DNS sinkhole**, not a code or availability problem — and it is subtle
because it makes the SSRF guard do exactly its job for the wrong reason.

1. Anna's md5 page lists several mirrors. The reliable one is `libgen.li/file.php?id=…`.
2. Many ISP/router resolvers (and Pi-hole/AdGuard lists) blackhole piracy domains: `libgen.li`
   resolves to `127.0.0.1` / `::1` instead of its real IP.
3. `security._is_safe_url` resolves the hostname and rejects loopback/internal IPs (anti-SSRF),
   so `libgen.li` is dropped **before any request** — you never see it tried in the logs.
4. The bot falls back to Anna's own endpoints, which are gated: `fast_download` → `302
   /fast_download_not_member` (paid membership), `slow_download` → `403` (DDoS-Guard JS challenge).
   All fail → `All mirrors failed for md5=…`.

**Confirm it:**
```bash
getent hosts libgen.li          # 127.0.0.1 → sinkholed;  a real IP (e.g. 179.43.167.164) → fine
nslookup libgen.li 1.1.1.1      # shows the real IP a public resolver returns
```

**Fix:** give the bot a non-filtering resolver. In Docker the shipped `docker-compose.yml` sets
`dns: [1.1.1.1, 8.8.8.8]` on the `bot` service (immune by default); otherwise set the Docker
daemon or host DNS to `1.1.1.1` / `8.8.8.8`. See the [README troubleshooting](../../README.md#troubleshooting).

> Note: the mirror list is scraped fresh from Anna on every download, so "mirrors changing" is
> never the real issue — only `libgen.li` being resolvable is.
