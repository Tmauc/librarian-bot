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
2. The model returns a JSON **Plan**: `{queries, language, format, title, series}` —
   one search query per volume, with canonical titles.
3. The flow confirms the plan, asks for one destination, then for each query:
   search → auto-pick the best result (EPUB-first) → download → deliver.

Plain single-title searches (`dune`) skip the LLM entirely — same behaviour as before.

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

## Notes

- **Model size matters.** A 0.5B model returns nonsense for enumeration; a 3B handles
  famous series well. Obscure series may still be missed — the batch just skips a
  volume it can't find.
- **Graceful fallback.** If `LLM_MODEL` is unset or the server is unreachable, the bot
  does a normal single search — no crash, no hang beyond the request timeout.
- **Cross-platform.** Ollama runs the model locally; on a Raspberry Pi prefer a small
  model or run it on the NUC.
