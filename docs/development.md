# Development

See the [architecture overview](architecture.md) first.

## Run locally

```bash
pip install -r requirements.txt
python main.py            # starts every configured client (Telegram and/or Discord)
```

Configure at least one client in `.env` — see [configuration](configuration.md).

## Tests & lint

```bash
pip install -r requirements-dev.txt
python -m pytest         # 81 tests
ruff check librarian main.py tests
```

CI runs both on every push/PR — see [`.github/workflows/tests.yml`](../.github/workflows/tests.yml).
Lint config is in [`ruff.toml`](../ruff.toml).

### What the tests cover

| Area | File |
|---|---|
| Conversion (real PDF; Calibre-only MOBI/AZW3) | `tests/test_converter.py` |
| SSRF guard | `tests/test_ssrf.py` |
| Streaming + cancel cleanup | `tests/test_cancel_cleanup.py` |
| Search fan-out / order / dedup | `tests/test_search_service.py` |
| End-to-end flow (fake client) | `tests/test_flow.py` |
| Telegram / Discord adapters | `tests/test_telegram_adapter.py`, `tests/test_discord_adapter.py` |
| Destinations (availability, dispatch) | `tests/test_destinations.py` |
| Pure helpers | `tests/test_pure_functions.py`, `tests/test_flow_logic.py` |

## Extending

- **Add a download source** → [sources/README.md#adding-a-source](sources/README.md#adding-a-source)
- **Add a client platform** → [clients/README.md#adding-a-client](clients/README.md#adding-a-client)
- **Add a destination** → [destinations.md#adding-a-destination](destinations.md#adding-a-destination)

All three are drop-ins: the `core/` package and `clients/flow.py` never change.

## Conventions

- Keep platform code inside its adapter (`clients/<platform>/`) and provider code inside its
  source (`sources/<name>.py`). The core stays neutral.
- Best-effort cleanup uses `contextlib.suppress`; cancellation cleanup catches `BaseException`
  (because `CancelledError` is not an `Exception`).
- User-facing strings are French; code, comments and these docs are English.

## Docker

```bash
docker compose up -d --build
docker compose logs -f bot
```

The image copies `main.py` + the `librarian/` package and runs `python main.py`.
