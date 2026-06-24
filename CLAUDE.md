# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A modular Telegram **userbot** (runs under a real user account via Telethon/MTProto, *not* the Bot API). The user types "markers" (e.g. `/draft`, `/fix`, `/auto_draft on`) into a chat's **input field** — the bot observes the synced draft, runs a feature, and writes the result back into the same draft (or edits a sent message). The user always reviews before sending. LLM calls (via `pydantic-ai`) are the only outbound data flow.

The central trick: markers are detected from Telegram's `UpdateDraftMessage` (text typed but *not* sent). If the user presses Send fast, no draft syncs and the marker is treated as a normal outgoing message and ignored.

## Commands

```bash
uv sync                                   # install deps + dev group
uv run pytest                             # full test suite
uv run pytest tests/unit/test_markers.py  # one file
uv run pytest -k draft                     # by keyword
uv run pytest tests/integration            # integration tests only
uv run ruff check                          # lint
uv run telegram-assistant --config config.toml --state state.toml   # run locally
```

`asyncio_mode = "auto"` (pyproject) — async tests need no `@pytest.mark.asyncio`. `pythonpath = ["src"]` so imports are `telegram_assistant.*`. Requires Python 3.12+.

Docker is the deployment path (see README); for code work use the `uv` commands above.

## Architecture

A small framework **core** dispatches Telegram events to pluggable **modules**. Adding a feature = one module package under `src/telegram_assistant/modules/<name>/` + a `[modules.<name>]` config block + one line in `module_loader._known_modules()`.

### Core (`src/telegram_assistant/`)

- `__main__.py` — CLI entry. Builds `TelethonTelegramClient`, `LLMFactory`, `App`; wires Telethon callbacks to `App.inject_*`; runs the app plus a config file-watcher in a `TaskGroup`.
- `app.py` — the glue. **Routing rules** (also documented in its docstring):
  - `DraftUpdate` whose text matches a registered marker → the winning module's `on_draft_update`.
  - `DraftUpdate` with no marker → broadcast to every module's `on_plain_draft_update` (used by pre-send autofix and draft-tracking).
  - `IncomingMessage` / `MessageEdited` / `OutgoingMessage` → broadcast to all modules' corresponding handlers.
  - Wraps the real client in `_LoopProtectingClient` so every `write_draft` we make is recorded; the matching inbound `DraftUpdate` is then suppressed (`loop_protection.py`) to avoid reacting to our own writes.
- `event_bus.py` — keys in-flight tasks by `(topic, module, chat_id)`. Dispatching a new event for the same key **cancels the previous still-running handler**. Modules must tolerate cancellation mid-run.
- `markers.py` — `Marker` (EXACT or CONTAINS match, with `priority`) + `MarkerRegistry`. On a draft, the registry picks **at most one** winner (highest priority, ties by registration order). Duplicate trigger strings across modules raise `DuplicateTriggerError` at load.
- `module.py` — the `Module` Protocol (all handlers optional via `hasattr` checks in `app.py`) and `ModuleContext` (the `tg`, `llm`, `http`, `config`, per-module `state`, `log` handed to each module at `init`).
- `config.py` — parses `config.toml` into frozen dataclasses. Env vars (`TELEGRAM_API_ID/HASH/SESSION`, `LLM_MODEL`) **override** file values. Placeholders like `api_id = 0` / `"YOUR_API_HASH"` are rejected as unset.
- `state.py` — `state.toml`, the **runtime** source of truth for per-chat toggles. Namespaced per module (`state.for_module(name)` → `ModuleState.get/set(bucket, key, default)`). The app is the sole writer; writes are atomic (temp + `os.replace`).
- `llm.py` — `LLMFactory` thin wrapper over `pydantic-ai` `Agent` with a timeout.
- `telethon_client.py` — the only place that touches Telethon; adapts Telethon events ↔ our `events.py` dataclasses. `telegram_client.py` is the `Protocol` it satisfies.

### Config vs. state

Two files, different writers (see README's config reference table):
- `config.toml` — user-written: credentials, model, module defaults, marker trigger overrides, **seed** whitelists.
- `state.toml` — app-written: live per-chat toggles set via in-chat markers. `_auto_on()` in modules reads the state override first, falling back to the config seed list. **state.toml wins at runtime.**

`config.example.toml` is the annotated template (copied to `/data/config.toml` on first Docker run by `entrypoint.sh`, which exits 2 to prompt editing).

### Modules (`src/telegram_assistant/modules/`)

- `drafting` — `/draft` (one-shot, overwrites the draft; if the marker is sent, edits the sent message in place like `/fix-in-sent`) + `/auto_draft on|off` (per-chat). Debounced auto-draft with cooldown; tracks whether the user is mid-typing (`on_plain_draft_update`) to avoid clobbering their input. Optional OpenAI-compatible backend via `[modules.drafting.openai]` (`openai_drafter.py`), else the `pydantic-ai` `Pipeline`.
- `correcting` — `/fix` (one-shot) + `/auto_fix` (pre-send) + `/auto_fix_sent` (post-send edit via Telegram edit API).
- `media_reply` — regex-matched URLs in whitelisted chats → `yt-dlp` download → reply with the media file. `/auto_media on|off`.
- `agent` — `/agent <instruction>`: debounced, fetches chat history, runs the LLM, posts the answer to a separate Telegram **bot** chat (`bot_sender.py`). "Send-disabled" if `bot_token_env` is unset (LLM still runs, result logged).

### Conventions for new modules

- Implement only the handlers you need (the Protocol's methods are all optional). Register triggers in `markers()`; keep `priority` higher for exact toggles, lower for `CONTAINS` instructions (see drafting: 100 for toggles, 50 for `/draft`).
- Read defaults from `ctx.config`, persist per-chat toggles through `ctx.state`, never write `state.toml` directly.
- Add the class to `module_loader._known_modules()` and document its config in `config.example.toml`.
- Because the bus cancels overlapping handlers per chat, treat `asyncio.CancelledError` as a normal abort (don't write partial drafts after cancellation).

## Testing patterns

- `tests/fakes/` — `FakeTelegramClient` (in-memory drafts/sent/edits/history) and a fake LLM; use these instead of mocking Telethon. `make_message(...)` builds `Message` fixtures.
- `tests/unit/` mirrors the source tree; `tests/integration/` drives flows end-to-end through `App` (e.g. `test_agent_flow.py`, `test_cross_module.py`).

## Git

Commit as `mcdax` (already the configured git user). Do **not** pass `-c user.email`/`user.name` overrides.

## Notes

- `.claude/worktrees/agent-module/` is a stale git worktree checkout — ignore it; the live source is under `src/`.
- Telethon `.session` files are host-bound: never copy one to another host; do a fresh interactive login per host.
