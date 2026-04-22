# telegram-assistant

## Overview

A modular Telegram **userbot** — it runs under your own Telegram account (MTProto, not the Bot API) and augments your chats with LLM-driven features. Markers you type into the chat input (e.g. `/draft`, `/fix`, `/auto_draft on`) trigger per-chat behaviour. All generated text lands as a native Telegram draft (or a message edit), so you always review and send manually.

**Features:**

- **Reply drafting** — one-shot `/draft` or per-chat auto-drafting on incoming messages, using conversation history + optional external context enrichment.
- **Self-correction** — one-shot `/fix` for grammar/spelling/punctuation, plus per-chat pre-send (`/auto_fix`) and post-send (`/auto_fix_sent`) modes.
- **Media-URL replies** — regex-matched URLs in whitelisted chats are downloaded via `yt-dlp` and replied with the media file.
- **Plugin architecture** — a small framework core (Telegram client, event bus, marker registry, config/state, LLM factory) with three built-in modules (`drafting`, `correcting`, `media_reply`). Adding a feature = one module file + a config block.

**Privacy:** LLM calls are the only outbound data flow. Chat content leaves your machine only to whichever provider matches `[llm].model` (Anthropic, OpenAI, Google, Ollama for local, etc. — whatever `pydantic-ai` supports). Enrichment is opt-in via a URL you control.

**Design docs:** `docs/superpowers/specs/2026-04-19-telegram-assistant-design.md`.
**Implementation plan:** `docs/superpowers/plans/2026-04-19-telegram-assistant.md`.

---

## Examples

All markers stay in your Telegram input field — **don't press Send**. Type the marker, pause briefly (your official client syncs the draft to the server, which we observe). The bot processes, and your input field updates with the result.

### `/draft` — one-shot reply

```
/draft
```

Pipeline: fetch recent chat history → optional enrichment POST → LLM → draft appears in your input. With an instruction:

```
/draft keep it short and ask about the invoice
```

The text before/after `/draft` becomes the instruction.

### `/fix <text>` — one-shot rewrite

```
/fix hi how ar you i wan to know wen you cam
```

Replaces the input with the LLM-corrected version. Preserves meaning, tone, language.

### `/auto_draft on` / `/auto_draft off` — per-chat auto-drafting

In a chat where you want auto-replies:

```
/auto_draft on
```

Confirmation appears (`✓ Auto-draft enabled for this chat`). Clear it. From then on, every incoming message in that chat triggers draft generation. Debounced: the first drafts immediately; subsequent incomings within `auto_draft_debounce_s` (default 60s) reset a silence timer. When you send your own reply, the cooldown resets.

The module also checks whether you currently have a non-empty draft in the chat — if yes, the auto-draft is skipped so it doesn't overwrite your typing.

### `/auto_fix on` / `/auto_fix off` — pre-send autofix

```
/auto_fix on
```

From then on, anything you type in that chat gets auto-corrected as you pause. Your input field visibly updates to the corrected version. Review + send as usual.

### `/auto_fix_sent on` / `/auto_fix_sent off` — post-send autofix

```
/auto_fix_sent on
```

Every message you send in that chat gets edited via Telegram's edit API after sending. Recipients briefly see the original and then the corrected version with an "edited" marker. Useful for casual chats where you type fast.

### `/auto_media on` / `/auto_media off` — per-chat media replies

```
/auto_media on
```

In that chat, any incoming message matching a configured URL regex (Instagram / TikTok / YouTube / etc.) triggers a download via `yt-dlp` and a reply with the media file. Handlers are configured under `[modules.media_reply.handlers]`.

---

## Setup guide

**Recommended: Docker.** Single-host deployment with persistent state and bundled `ffmpeg`.

### 1. Prerequisites

- Docker + `docker compose v2`
- Telegram API credentials: `api_id` and `api_hash` from https://my.telegram.org → API development tools
- An LLM provider API key (e.g. Anthropic, OpenAI)

### 2. Secrets

```bash
cp .env.example .env
```

Open `.env` and fill in the LLM provider API key that matches your chosen model (for example `ANTHROPIC_API_KEY=sk-...`). Optionally set `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION`, and `LLM_MODEL` here as well — env vars override values in `config.toml`.

### 3. Build the image

```bash
docker compose build
```

### 4. Bootstrap the config

```bash
docker compose run --rm telegram-assistant
```

On first run against an empty `./data/`, the container writes `./data/config.toml` from the commented template, prints an edit instruction, and exits with code 2.

Edit `./data/config.toml`:
- `[telegram].api_id` / `api_hash` — unless you set them in `.env`
- `[llm].model` — e.g. `anthropic:claude-sonnet-4-6`
- `[modules.drafting].auto_draft_chats`, `[modules.media_reply].chats` — optional seed whitelists (the runtime source of truth is `state.toml`, toggled via markers in-chat)
- `[modules.drafting].enrichment_url` — optional external context endpoint

### 5. Telethon login (interactive, one-time)

```bash
docker compose run --rm telegram-assistant
```

Telethon prompts for your phone number, login code, and 2FA password if set. The session saves to `./data/assistant.session`. Press **Ctrl-C** once you see the assistant connect.

> **Do not skip this step with `docker compose up`.** `up` does not forward stdin, so Telethon's prompt has nowhere to read the phone number. The container silently hangs after `Connection complete!` with an empty session file. If that happens, run `docker compose down` and redo step 5.

### 6. Run detached

```bash
docker compose up -d
docker compose logs -f
```

This only works after step 5 has populated a valid session file. Open Telegram, type a marker somewhere (see **Examples** above), and watch the logs.

### Updates

```bash
docker compose build
docker compose up -d
```

`./data/` is untouched — your config, runtime state (per-chat toggles), and Telethon session all persist across rebuilds.

### Configuration reference

Your live configuration lives in two files under `./data/`:

| File | Who writes it | Contents |
|---|---|---|
| `config.toml` | you | Telegram credentials, LLM model, module defaults, marker triggers, initial whitelists |
| `state.toml` | the app | Runtime per-chat overrides: `auto_draft`, `auto_fix`, `auto_fix_sent`, `auto_media` |

`config.example.toml` at the repo root is the annotated template copied by the first-run bootstrap. Read it for the full set of keys.

### Alternate: host Python (development)

For local work without Docker:

```bash
uv sync
cp config.example.toml config.toml   # then edit with real creds
uv run telegram-assistant --config config.toml --state state.toml
```

Run the test suite:

```bash
uv run pytest
```

### Troubleshooting

**Container logs stop at `Connection … complete!` and nothing else happens.**
Telethon is blocked on interactive login that `docker compose up` cannot provide.
```bash
docker compose down
docker compose run --rm telegram-assistant   # complete login, then Ctrl-C
docker compose up -d
```

**`sqlite3.OperationalError: unable to open database file` on startup.**
`./data` on the host isn't writable by UID 1000 (the `app` user inside the container).
```bash
sudo chown -R 1000:1000 ./data
```

**`ConfigError: missing telegram.api_id` (or `api_hash`) despite values in `config.toml`.**
`api_id = 0` and `api_hash = "YOUR_API_HASH"` are rejected as unset placeholders. Edit `./data/config.toml` with real values, or set `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` in `.env`.

**Markers don't do anything when I send them as messages.**
Markers must stay in your chat input field, not be sent. Type the marker, **pause** (don't press Send) — your official client syncs the draft to the server, the bot observes it via `UpdateDraftMessage`, processes, and overwrites the input. If you press Send quickly, Telegram bypasses the draft and delivers the message as-is; the bot treats it as a regular outgoing message and ignores it.

**Debugging: verbose logs.**
Set `LOG_LEVEL=DEBUG` in `.env` and restart. You'll see every event traced through the pipeline (Telethon event → app routing → module handler → LLM call → write). Very useful to understand why a marker did or didn't trigger.
