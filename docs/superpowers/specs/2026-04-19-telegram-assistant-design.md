# Telegram Assistant — Design

**Date:** 2026-04-19
**Status:** Draft for review

## 1. Purpose

A Telegram **userbot** (runs under the user's own account via MTProto, not the Bot API) that provides a small plugin framework for features that react to Telegram events. The first features are reply drafting, self-text correction, and automatic media replies for URLs that match configured patterns. New features are added as new modules without touching the core.

## 2. Scope

- **Pluggable modules** over a shared core (Telegram client, event bus, config, runtime state, LLM factory, HTTP session).
- Built-in modules:
  - **`drafting`** — draft replies using chat history + external enrichment + LLM; triggered by an incoming message in whitelisted chats or by a `/draft` marker. Also owns `/auto on` and `/auto off` markers that toggle auto-draft per chat at runtime.
  - **`correcting`** — rewrite the user's own text (grammar/spelling/punctuation) on a `/fix` marker.
  - **`media_reply`** — when an incoming message matches a configured URL regex, download the media and post it as a reply.
- Works across **1:1 DMs, groups, and channels**.
- Config lives in `config.toml` (user-authored) + `state.toml` (app-managed).

Out of scope: sending on the user's behalf without an explicit trigger, message moderation, multi-user deployments, Bot API integration, remote plugin discovery.

## 3. Architecture

### 3.1 Overview

Single-process Python 3.12 asyncio application. A small **core** wires together Telegram I/O, event dispatch, config, and shared services. Features are **modules** that register handlers against the event bus; the core does not know what any module does.

```
            ┌──────────────────────── telegram_client (Telethon) ────────────────────────┐
            │           emits                                                              │
            │    IncomingMessage, DraftUpdate                                              │
            └────────────────────────────────────────┬─────────────────────────────────────┘
                                                     │
                                             ┌───────▼────────┐
                                             │   event_bus    │
                                             └───────┬────────┘
                        ┌────────────────────────────┼────────────────────────────┐
                        │                            │                            │
                ┌───────▼────────┐          ┌────────▼───────┐          ┌─────────▼────────┐
                │   drafting     │          │  correcting    │          │   media_reply    │
                │  (module)      │          │   (module)     │          │    (module)      │
                └────────────────┘          └────────────────┘          └──────────────────┘

Shared services (ModuleContext): telegram_client, llm_factory, http_session, config, runtime_state, logger
```

### 3.2 Core components

- **`telegram_client`** — Telethon wrapper. Subscribes to `UpdateNewMessage` and `UpdateDraftMessage`. Exposes:
  - `send_message(chat_id, text=None, reply_to=None, files=None)`
  - `write_draft(chat_id, text)`
  - `fetch_history(chat_id, n) -> list[Message]`
  - `download_media(message) -> Path`
  - emits `IncomingMessage` and `DraftUpdate` events onto the event bus.
- **`event_bus`** — async dispatcher. For each event, invokes registered handlers. Handlers execute concurrently per event but one event at a time per `(module, chat_id)` to prevent overlapping generations.
- **`marker_registry`** — modules register draft-text markers with a priority. On `DraftUpdate`, the registry identifies the single winning marker (highest priority, first occurrence on ties) and dispatches only to that module's `on_draft_update`. Drafts without any registered marker are ignored.
- **`module_loader`** — at startup, reads the `[modules.*]` sections in `config.toml`, instantiates each enabled module with a `ModuleContext`, calls `init()`, and registers its event handlers.
- **`config_loader`** — loads `config.toml`; watches for changes via `watchfiles`; hot-reloads by rebuilding module configs and invoking per-module `reconfigure()` where implemented.
- **`runtime_state`** — reads/writes `state.toml` atomically. Exposes a namespaced API so each module gets its own keyspace (e.g. `state.for_module("drafting").get("auto_draft")`).
- **`loop_protection`** — shared helper: `last_written_draft[chat_id]` cache. Modules consult this before reacting to a `DraftUpdate` they may have written themselves.

### 3.3 Module interface

```python
class Module(Protocol):
    name: str

    async def init(self, ctx: ModuleContext) -> None: ...
    async def reconfigure(self, new_config: dict) -> None: ...
    async def shutdown(self) -> None: ...

    # Optional event handlers — implement only what you need.
    def markers(self) -> list[Marker]: ...
    async def on_incoming_message(self, event: IncomingMessage) -> None: ...
    async def on_draft_update(self, event: DraftUpdate, marker: Marker) -> None: ...
```

`ModuleContext` holds: `tg` (telegram_client), `llm` (Pydantic AI factory — `llm.agent(system_prompt, ...)`), `http` (shared `aiohttp.ClientSession`), `config` (this module's config dict), `state` (namespaced runtime state), `log` (logger).

`Marker` carries `name`, `pattern` (exact token or regex), `priority`, and a `parse(text) -> (remainder, ok)` hook for modules that need custom parsing (e.g., `/auto on`).

### 3.4 Startup

1. Parse CLI args (`--config`, `--state`, defaults: `./config.toml`, `./state.toml`).
2. Load config. Validate required sections (`[telegram]`, `[llm]`, `[modules]`).
3. Connect Telethon (interactive first-run login; subsequent runs use the session file).
4. Instantiate each module listed under `[modules]` with `enabled = true`. Collect their markers into `marker_registry`. Register their handlers on `event_bus`.
5. Start the event loop.

## 4. Built-in modules

### 4.1 `drafting`

**Purpose:** draft replies in the user's voice, and own the per-chat auto-draft toggle.

**Events handled:**
- `on_incoming_message` — runs the drafting pipeline if effective auto-draft is `on` for the chat (§4.1.4).
- `on_draft_update` — for three markers.

**Markers registered:**

| Marker       | Priority | Action                                                            |
|--------------|----------|-------------------------------------------------------------------|
| `/auto on`   | 100      | Enable auto-draft for this chat.                                  |
| `/auto off`  | 100      | Disable auto-draft for this chat.                                 |
| `/draft`     | 50       | Run drafting pipeline with remainder as extra user instruction.   |

Higher priority wins. Exact match (case-insensitive, whitespace-trimmed) is required for `/auto on` and `/auto off` — garbage suffixes are ignored (logged).

#### 4.1.1 `/draft` flow

1. `trigger_parser` strips the `/draft` token. The concatenated remainder (text before + after) is the optional user instruction.
2. Fetch last-N messages from the chat (`ctx.tg.fetch_history`).
3. POST to the enrichment endpoint (best-effort — §4.1.5).
4. Call `ctx.llm.agent(resolved_system_prompt).run(...)` with: `system_prompt + enrichment + history + user_instruction`.
5. `ctx.tg.write_draft(chat_id, output)`; update `loop_protection[chat_id]`.

#### 4.1.2 Auto-draft flow

1. On `IncomingMessage`, skip if:
   - The message is from the user themselves (outgoing), or
   - Effective auto-draft for this chat is `off` (§4.1.4).
2. Otherwise run steps 2–5 above, omitting the user instruction.

#### 4.1.3 `/auto on` / `/auto off` flow

1. Set `ctx.state.set("auto_draft", chat_id, True|False)` (namespaced to `drafting`).
2. Overwrite the draft with `✓ Auto-draft enabled for this chat` or `✓ Auto-draft disabled for this chat`.
3. Update `loop_protection`. No LLM, no history, no enrichment.

#### 4.1.4 Effective auto-draft state (per chat)

Resolution order:

1. `state.toml.drafting.auto_draft."<chat_id>"` if set → use it.
2. Else `<chat_id>` in `config.toml.modules.drafting.auto_draft_chats` → `true`.
3. Else → `false`.

`config.toml.modules.drafting.auto_draft_chats` seeds the initial state; `state.toml` is runtime authority. The app never writes `config.toml`.

#### 4.1.5 Enrichment endpoint (scoped to this module)

- Request:
  ```json
  POST {enrichment_url}
  Authorization: {enrichment_auth_header}   (optional)
  {
    "chat_id": 12345,
    "chat_title": "Alice",
    "messages": [
      {"sender": "Alice", "timestamp": "2026-04-19T10:12:00Z", "text": "..."},
      {"sender": "me",    "timestamp": "2026-04-19T10:13:00Z", "text": "..."}
    ]
  }
  ```
- Response: `{ "context": "..." }` — opaque string concatenated into the LLM prompt.
- Best-effort: timeout or non-2xx → empty context, warning logged, drafting continues.

#### 4.1.6 System prompt resolution

Per chat: `config.modules.drafting.chats.<id>.system_prompt` if set, else `config.modules.drafting.default_system_prompt`. Same rule for `last_n`.

### 4.2 `correcting`

**Purpose:** rewrite the user's own text for grammar, spelling, punctuation; preserve meaning, tone, and language.

**Events handled:** `on_draft_update` only.

**Markers registered:**

| Marker | Priority | Action                                  |
|--------|----------|-----------------------------------------|
| `/fix` | 70       | Rewrite the remainder using the LLM.    |

**Flow:**

1. Strip `/fix`; remainder is the text to correct.
2. If remainder is empty/whitespace → ignore (logged).
3. `ctx.llm.agent(correcting.system_prompt).run(remainder)`.
4. `ctx.tg.write_draft(chat_id, output)`; update `loop_protection`.

No history, no enrichment, no per-chat override.

### 4.3 `media_reply`

**Purpose:** when an incoming message contains a URL matching one of the configured regexes, download the media and reply with it.

**Events handled:** `on_incoming_message` only. No markers.

**Flow:**

1. On `IncomingMessage` in an allowed chat (see below), scan `event.text` against the module's configured handlers in order. First match wins.
2. Extract the URL, call the handler's **backend** (`yt_dlp` initially) to download the media to a temp directory.
3. Decide output mode per config (`send_as`):
   - `"reply"` → `ctx.tg.send_message(chat_id, files=[path], reply_to=event.message_id)`.
   - `"draft"` → write a draft carrying the media caption + note; user sends manually. (Drafts can carry media in Telegram, but this is a stretch goal — initial implementation supports `"reply"` only; `"draft"` is documented as a future extension.)
4. Clean up the temp file.

**Chat scope:** `modules.media_reply.chats` is a whitelist of chat IDs. Empty list = disabled (safer default than "all chats"). The user opts in per chat.

**Handler config (one block per URL family):**

```toml
[[modules.media_reply.handlers]]
name = "instagram"
pattern = 'https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[\w-]+'
backend = "yt_dlp"

[[modules.media_reply.handlers]]
name = "tiktok"
pattern = 'https?://(?:www\.)?tiktok\.com/@[\w.-]+/video/\d+'
backend = "yt_dlp"
```

**Backends** are small strategy classes (`YtDlpBackend`, later potentially others) behind a minimal interface:

```python
class DownloadBackend(Protocol):
    async def download(self, url: str, dest_dir: Path) -> Path: ...
```

**Failures:**
- Download timeout / error → log warning, no reply sent. Do not draft an error into the chat.
- Multiple URLs in one message → first match only (simpler; revisit if a real need arises).

## 5. Configuration

### 5.1 `config.toml` (user-authored)

```toml
[telegram]
api_id = 0
api_hash = ""
session = "assistant"

[llm]
model = "anthropic:claude-sonnet-4-6"
timeout_s = 30

[modules.drafting]
enabled = true
default_system_prompt = """You are drafting a reply in the user's voice. Casual, concise, matches the conversation's tone."""
last_n = 20
auto_draft_chats = []         # initial seed; runtime truth is state.toml
enrichment_url = "https://example.internal/context"
enrichment_auth_header = "Bearer ..."   # optional
enrichment_timeout_s = 10

# Optional per-chat overrides for drafting.
[modules.drafting.chats."12345"]
system_prompt = "..."
last_n = 50

[modules.correcting]
enabled = true
system_prompt = """Rewrite the given text correcting grammar, spelling, and punctuation. Preserve meaning, tone, and language. Output only the rewritten text."""

[modules.media_reply]
enabled = true
chats = []                    # whitelist; empty = module inactive
send_as = "reply"             # "reply" (initial) | "draft" (future)
download_timeout_s = 60

[[modules.media_reply.handlers]]
name = "instagram"
pattern = 'https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[\w-]+'
backend = "yt_dlp"
```

Notes:
- TOML bare keys allow digits; quote negative (supergroup/channel) IDs: `[modules.drafting.chats."-1001234567890"]`.
- The `[markers]` table from earlier drafts is gone — markers are owned by the modules that register them.

### 5.2 `state.toml` (app-managed)

```toml
[drafting.auto_draft]
"12345" = true
"-1001234567890" = false
```

- Written atomically (`tmp + rename`) on mutation.
- Not watched; only we write it.
- Namespaced by module name so other modules can add their own state later.

### 5.3 Secrets

`api_id`, `api_hash`, `llm` credentials (Pydantic AI reads them from env), and `enrichment_auth_header` are sensitive. `config.toml` and `state.toml` are git-ignored. `config.example.toml` is checked in.

### 5.4 Hot reload

`watchfiles` observes `config.toml`. On change:

1. Parse new config; keep the previous one if invalid (log error).
2. For each enabled module:
   - If newly enabled → instantiate and `init()`.
   - If now disabled → `shutdown()` and deregister.
   - Otherwise → `reconfigure(new_config)`.
3. Re-register markers.

Modules implement `reconfigure` for safe in-place updates; those that can't, document themselves as requiring a restart.

## 6. LLM invocation (shared via `ctx.llm`)

- `ctx.llm.agent(system_prompt: str) -> pydantic_ai.Agent` returns a configured agent using the global `[llm].model`.
- Each call wrapped in `asyncio.wait_for(..., llm.timeout_s)`. Timeout → module logs and skips the write.
- Modules are free to configure their own `Agent` (tools, structured output) if needed — `drafting` and `correcting` both use plain text generation.

## 7. Error handling

| Failure                           | Behaviour                                                                 |
|-----------------------------------|---------------------------------------------------------------------------|
| Telethon disconnect               | Auto-reconnect; core re-subscribes updates.                               |
| Config invalid on reload          | Keep previous config; warn in logs.                                       |
| Module `init` or `reconfigure` raises | Log; leave module in previous state. Other modules unaffected.         |
| LLM timeout or exception          | Module logs; no draft written. Other modules unaffected.                  |
| Enrichment failure (drafting)     | Empty context; drafting continues.                                        |
| `state.toml` write fails (drafting) | Keep in-memory change; log; write `✗ state write failed` as confirmation. |
| `media_reply` download fails      | Log; no reply sent.                                                       |
| Marker with no registered handler | Ignored (no-op).                                                          |

## 8. Testing

- **Unit (no network, no LLM):**
  - Core: `marker_registry` precedence + exact-match semantics; `event_bus` ordering; `loop_protection`; `runtime_state` atomic write and namespacing; `config_loader` reload.
  - `drafting`: marker parsing (`/draft`, `/auto on`, `/auto off`), effective-state resolution, auto-draft skip for outgoing messages, enrichment mocked via `aioresponses`, LLM via Pydantic AI `TestModel`.
  - `correcting`: marker parsing, empty remainder ignored, LLM via `TestModel`.
  - `media_reply`: regex matching, chat whitelist, handler order, backend stubbed.
- **Integration:**
  - Fake Telethon client fixture that emits `UpdateDraftMessage` and `UpdateNewMessage`; assert correct module handles the event and the expected Telegram call is made.
  - End-to-end tests per module; cross-module test (media_reply + drafting on the same incoming message — both run, order doesn't matter).
- **CI** never talks to Telegram, LLM APIs, or real download endpoints.

## 9. Stack

- Python 3.12
- `uv` for packaging
- `telethon` — MTProto client
- `pydantic-ai` — LLM abstraction
- `aiohttp` — shared HTTP session
- `watchfiles` — config hot-reload
- `tomllib` / `tomli-w` — config read / state write
- `yt-dlp` — initial media download backend
- `pytest` + `pytest-asyncio` + `aioresponses` — tests

No SQLite, no database. Persistent files: `config.toml`, `state.toml`, Telethon session file.

## 10. Non-goals and tradeoffs

- **Draft-sync latency.** `UpdateDraftMessage` fires after the official client debounces; users pause briefly after typing a marker. Accepted to avoid send-then-delete races.
- **No cross-module event consumption.** All modules see every `IncomingMessage`. `media_reply` replying with a video does not suppress `drafting` from also producing a reply draft on the same incoming message. If that becomes a problem, add explicit per-module conditions (e.g., "if any other module produced output, skip auto-draft") later.
- **No remote module discovery.** Modules are Python packages loaded in-process. Adding one means adding code.
- **Single inflight handler per `(module, chat_id)`.** A new trigger while the previous is running cancels the previous. Simpler than queueing.
- **`media_reply` draft-mode is deferred.** Initial release supports `send_as = "reply"` only.

## 11. Open items for implementation plan

- Concrete `Marker` API (exact-match vs. regex, priority ordering, module binding).
- Sender-identifier format sent to the enrichment endpoint (username / display name / Telegram ID).
- Packaging: single entrypoint (`uv run telegram-assistant`), systemd example, log destination.
- `yt-dlp` invocation boundary: subprocess vs. Python API; cookie/auth handling for Instagram is a known pain point — may require per-handler cookies file referenced from config.
- Default priorities: whether `/auto` should stay above `/draft` and `/fix` (currently 100 > 70 > 50).
- Whether to add a `/help` marker that lists all registered markers (probably yes; trivial and high-value).
