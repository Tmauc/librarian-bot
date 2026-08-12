# Sources (download providers)

A **source** is a pluggable provider that can search for books and download them. The core
fans out to every enabled source and merges the results; each source fully owns how it
fetches its own results.

Available sources:

- [Anna's Archive](anna-archive.md)
- [Prowlarr](prowlarr.md)

See also: [architecture](../architecture.md) · [configuration](../configuration.md).

## The contract: `Source`

Defined in [`librarian/sources/base.py`](../../librarian/sources/base.py).

| Member | Purpose |
|---|---|
| `name` | Unique registry name; stored on each `SearchResult` it produces. |
| `enabled` | Whether it is configured/usable (disabled sources are skipped). |
| `search(query) -> list[SearchResult]` | Return matches; fail soft (return `[]`, don't raise). |
| `download(result, on_progress, max_bytes) -> str` | Fetch to a local file; raise on failure. |
| `details(result) -> dict` | Optional richer metadata for the detail card (description/cover). Default `{}`. |
| `available(result) -> bool` | Cheap, best-effort liveness probe. Simple search pre-filters dead-mirror books out of the offered list with it. Default `True`; **must fail soft** (return `True` on error). |

### `SearchResult`

Defined in [`librarian/core/models.py`](../../librarian/core/models.py). Typed fields the core
uses — `source`, `title`, `ext`, `author`, `size_bytes`, `is_torrent` — plus **`ref`**, an
opaque dict the core never inspects. Put whatever your source needs to download later (md5,
guid, direct URL…) in `ref`. This is what keeps the core from knowing anything source-specific.

## How results are combined

[`core/search_service.py`](../../librarian/core/search_service.py) runs every enabled source's
`search()` in parallel, then:

1. orders results (e-reader formats first, direct before torrents),
2. drops results already known to exceed the client's file-size limit,
3. deduplicates by full normalized title (keeps distinct series volumes).

A download dispatches back through [`core/download_service.py`](../../librarian/core/download_service.py),
which looks the source up by `result.source` in the registry.

## <a name="adding-a-source"></a>Adding a source

1. Create `librarian/sources/<name>.py` with a `Source` subclass. Return `SearchResult`s with
   your handles in `ref`; implement `download()` (reuse
   [`core/netfetch.stream_to_tempfile`](../../librarian/core/netfetch.py) for HTTP streaming and
   [`core/security._is_safe_url`](../../librarian/core/security.py) for SSRF safety).
2. Add it to `_ALL` in [`librarian/sources/registry.py`](../../librarian/sources/registry.py)
   (or call `register()` at startup).
3. Add any config to [`config.py`](../../librarian/config.py) and [`.env.example`](../../.env.example).
4. Add a doc page here and link it from [the index](../README.md) and this list.
5. Add tests (parsing helpers, and merge behaviour via `tests/test_search_service.py`).

No core or client code changes.
