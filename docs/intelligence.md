# Intelligence (optional local LLM)

An optional layer that understands natural-language requests like
*« l'intégrale du Seigneur des anneaux en VF »* and turns them into a **batch**: it
plans the volumes, searches each, auto-picks the best result, and downloads/delivers
them all. Entirely optional and **local** — no API key, nothing leaves your machine.

See also: [architecture](architecture.md) · [configuration](configuration.md).

## How it works

1. A message that looks like a multi-book request (contains *intégrale*, *tous les
   tomes*, *saga*, *trilogie*…) is sent to a local LLM
   ([`core/planner.py`](../librarian/core/planner.py)).
2. The model returns a small JSON **Plan**: `{query, language, format, series}` — the
   **series/book name** (cleaned of « je veux… en VF »), a language hint, and whether
   it's a multi-book request. **It does NOT enumerate volumes** — a small local model
   hallucinates titles for niche series.
3. The flow searches the **real catalogue** (Anna's Archive) for that query, and the
   user **multi-selects** the actual volumes to download. Each selected book is then
   downloaded and delivered to one destination.

Plain single-title searches (`dune`) skip the LLM entirely — same behaviour as before.

Because the model only *extracts an intent* (not book knowledge), even a small model
does the job; the real volumes always come from the catalogue.

## Setup (Ollama)

1. Install [Ollama](https://ollama.com) (`brew install ollama` on macOS, or the app).
2. Pull a model — **~3B is recommended** for reliable series enumeration; smaller
   models hallucinate volume lists:
   ```bash
   ollama pull qwen2.5:3b      # or llama3.2:3b
   ```
3. Point the bot at it in `.env`:
   ```
   LLM_BASE_URL=http://localhost:11434   # default
   LLM_MODEL=qwen2.5:3b                  # empty = feature disabled
   ```

| Variable | Description |
|---|---|
| `LLM_MODEL` | Ollama model name. Empty = disabled (plain search). |
| `LLM_BASE_URL` | Ollama server URL (default `http://localhost:11434`). |

## Model choice

Since the model only extracts an intent (series name + language), the requirement is light —
**`qwen2.5:3b`** and **`gemma2:2b`** both work well; the lighter `gemma2:2b` (1.6 GB) is a fine
low-resource pick. Avoid `phi3.5` (over-confident) and stick to a clean instruction-follower.

> Historical note: an earlier version asked the model to *enumerate* the volumes. That worked
> only for very famous series and hallucinated niche ones (e.g. « Les Chevaliers d'Émeraude »),
> so enumeration was dropped in favour of searching the real catalogue.
- **Graceful fallback.** If `LLM_MODEL` is unset or the server is unreachable, the bot
  does a normal single search — no crash, no hang beyond the request timeout.
- **Cross-platform.** Ollama runs the model locally; on a Raspberry Pi prefer a small
  model or run it on the NUC.
