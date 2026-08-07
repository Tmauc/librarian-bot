# librarian-bot documentation

An ebook search/download bot built on a **ports & adapters** core: the domain logic
knows nothing about any messaging platform or any download provider. Adding either is
a drop-in.

## Start here

- **Just want to run it?** → [English install guide](../README.md) · [Guide FR (débutant)](../LISEZMOI.md)
- **How it fits together** → [Architecture](architecture.md)
- **Every setting** → [Configuration reference](configuration.md)

## Clients (messaging front-ends)

How users talk to the bot. Each is a thin adapter over the generic flow.

- [Client adapters — overview & how to add one](clients/README.md)
- [Telegram](clients/telegram.md)
- [Discord](clients/discord.md)

## Sources (download providers)

Where books come from. Each implements the `Source` contract and is registered once.

- [Sources — overview & how to add one](sources/README.md)
- [Anna's Archive](sources/anna-archive.md)
- [Prowlarr](sources/prowlarr.md)

## Delivery (where the file ends up)

- [Delivery: this chat, email, and Send to Kindle](delivery.md)

## Contributing

- [Development: run, test, lint, CI, conventions](development.md)

---

> **Note on language:** these technical docs are in English to match the codebase and
> `CLAUDE.md`. The step-by-step beginner guide is available in French as
> [`LISEZMOI.md`](../LISEZMOI.md).
