# telegram-assistant

## Overview

A modular Telegram **userbot** — it runs under your own Telegram account (MTProto, not the Bot API) and augments your chats with LLM-driven features. Markers you type into the chat input (`/draft`, `/fix`, `/agent`) trigger one-shot actions; per-chat automatic features are switched on and off in the **settings menu** (`/settings` to the bot). All generated text lands as a native Telegram draft (or a message edit), so you always review and send manually.

**Features:**

- **Reply drafting** — one-shot `/draft` or per-chat auto-drafting on incoming messages, using conversation history + optional external context enrichment.
- **Self-correction** — one-shot `/fix` for grammar/spelling/punctuation, plus per-chat pre-send (auto-fix) and post-send (auto-fix-sent) modes.
- **Media-URL replies** — regex-matched URLs in whitelisted chats are downloaded via `yt-dlp` and replied with the media file.
- **Settings menu** — *the* way to switch per-chat features: inline buttons in the agent bot's chat (`/settings`), with Telegram's native person/group picker and an overview of everything that's on.
- **Plugin architecture** — a small framework core (Telegram client, event bus, marker registry, config/state, LLM factory) with built-in modules (`drafting`, `correcting`, `media_reply`, `agent`, `settings_menu`). Adding a feature = one module file + a config block.

**Privacy:** Chat context is sent to the configured LLM; optional meme rendering also sends caption text to memegen.link (see below). Chat content leaves your machine only to whichever provider matches `[llm].model` (Anthropic, OpenAI, Google, Ollama for local, etc. — whatever `pydantic-ai` supports). Enrichment is opt-in via a URL you control.

Chat-history sender labels use the Telegram display name (first/last name or channel title), falling back to username, then ID when unavailable. Missing sender entities are resolved on demand; lookup failures do not discard messages. The username is also retained separately as `sender_username` (or `null`), alongside `sender` and `sender_id` in structured history payloads for meme, agent and OpenAI drafting.

**Design docs:** `docs/superpowers/specs/2026-04-19-telegram-assistant-design.md`.
**Implementation plan:** `docs/superpowers/plans/2026-04-19-telegram-assistant.md`.

---

## Examples

### Per-chat features: `/settings` in the bot chat

Automatic per-chat features are switched exclusively in the settings menu of the agent bot — there are no in-chat toggle markers.

1. Open the chat with the agent bot and send `/settings` (or `/start`; the command is registered in the bot's menu at startup).
2. Tap **👤 Person wählen** or **👥 Gruppe wählen** and pick the chat in Telegram's native picker.
3. The bot replies with `⚙️ Einstellungen für: <Name>` and one ✅/❌ button per feature — tap to toggle; it takes effect immediately.
4. **📋 Übersicht** lists every chat with at least one feature on, each with a button to open its menu.

| Button | Feature |
|---|---|
| *Antwortvorschläge* (`auto_draft`) | Every incoming message triggers draft generation. Debounced: the first drafts immediately; subsequent incomings within `auto_draft_debounce_s` (default 60s) reset a silence timer. When you send your own reply, the cooldown resets. Skipped while you have a non-empty draft in the chat, so your typing is never overwritten. |
| *Korrektur vor dem Senden* (`auto_fix`) | Anything you type in that chat gets auto-corrected as you pause; your input field visibly updates. Review + send as usual. |
| *Korrektur nach dem Senden* (`auto_fix_sent`) | Every message you send gets edited via Telegram's edit API after sending. Recipients briefly see the original, then the corrected version with an "edited" marker. |
| *Medien-Antworten* (`auto_media`) | Incoming messages matching a configured URL regex (Instagram / TikTok / YouTube / …) are downloaded via `yt-dlp` and replied with the media file. Handlers live under `[modules.media_reply.handlers]`. |

Only features of loaded modules are shown. A button writes the module's override to `state.toml`; the effective value is that override, else the config seed list (`auto_draft_chats`, `[modules.media_reply].chats`).

Requires the agent bot (`[modules.agent].bot_token_env`, by default `AGENT_BOT_TOKEN`) and your own user id as `[modules.agent].target_chat_id` (or `[modules.settings_menu].owner_user_id`). Only your updates are processed. The module long-polls `getUpdates`, so no other process (and no webhook) may consume that bot's updates. Without a token it stays inert.

### One-shot markers

These markers stay in your Telegram input field — **don't press Send**. Type the marker, pause briefly (your official client syncs the draft to the server, which we observe). The bot processes, and your input field updates with the result.

#### `/draft` — one-shot reply

```
/draft
```

Pipeline: fetch recent chat history → optional enrichment POST → LLM → draft appears in your input. With an instruction:

```
/draft keep it short and ask about the invoice
```

The text before/after `/draft` becomes the instruction. If you do send it, the sent message is edited in place with the generated reply.

#### `/fix <text>` — one-shot rewrite

```
/fix hi how ar you i wan to know wen you cam
```

Replaces the input with the LLM-corrected version. Preserves meaning, tone, language. If sent, the sent message is edited in place.

#### `/agent <instruction>`

Runs the agent over the chat history and posts its answer to the agent bot chat (see `[modules.agent]` in `config.example.toml`).

#### `/meme [beschreibung]`

```
/meme wenn der Montag schon wieder anfängt
```

The description is optional: typing just `/meme` lets the model choose the topic, template and joke from the chat history. With no usable context it chooses a relatable everyday situation. It still only creates a preview; sending into the source chat requires the **✅ Senden** button.

Clears the draft and sends the description plus the last 20 messages of the chat to your configured model. The module fetches the [memegen.link](https://memegen.link) template catalogue itself (cached for 24 hours) and supplies a complete system prompt: meme-writing rules, JSON output format, template IDs, exact field counts and examples. No custom agent, skill or scheduled template updater is needed. It validates the answer before rendering via memegen.link and posts a preview to the agent bot chat. Buttons: **✅ Senden** sends the image from your account into the original chat, **🔁 Nochmal** makes a new variant, **🎲 Anderes Template** picks a different template. Reply directly to a preview photo to request a targeted correction (e.g. “Only NACH Edeka, no other text”). Corrections apply only to that meme and are preserved in subsequent variants; the latest correction takes precedence. The original preview stays usable if the correction fails. Old previews expire after `pending_ttl_s` or a restart. Needs `[modules.meme]` enabled and `settings_menu` running (it polls the bot's button taps); see `config.example.toml`.

Use any OpenAI-compatible endpoint via `[modules.meme.openai]` (OpenAI, OpenRouter, Ollama or OpenClaw), or omit that block to use `[llm]`. Set `base_url`, `model` and an environment variable containing the API key; see the example config. A failed catalogue refresh reuses the last successful list; the first request fails clearly if no list can be fetched. Invalid JSON, unknown IDs or wrong field counts get one corrective retry. Custom background images require a reachable image URL; web search is available only if your backend supplies tools. Only generic meme terms, never chat content, may be used for web search.

**Meme privacy:** the selected LLM receives the description and chat context. memegen.link receives the caption text and, for custom memes, the background URL, but not the chat history.

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
- `[modules.drafting].auto_draft_chats`, `[modules.media_reply].chats` — optional seed whitelists (the runtime source of truth is `state.toml`, toggled per chat via `/settings` in the bot chat)
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
| `state.toml` | the app | Runtime per-chat overrides (set via the `/settings` menu): `auto_draft`, `auto_fix`, `auto_fix_sent`, `auto_media` |

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
