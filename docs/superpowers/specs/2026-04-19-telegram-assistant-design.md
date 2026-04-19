# Telegram Assistant — Design

**Date:** 2026-04-19
**Status:** Draft for review

## 1. Purpose

A Telegram **userbot** (runs under the user's own account via MTProto, not the Bot API) that drafts replies and rewrites messages in the user's own Telegram client, triggered by markers the user types into the chat input. Generated text is written as a native per-chat Telegram draft, so the user always reviews and sends manually.

## 2. Scope

- **Drafting replies** to incoming conversations using the chat's recent history, an external context-enrichment endpoint, and an LLM.
- **Correcting the user's own text** (grammar, spelling, punctuation) without involving chat history.
- Works across **1:1 DMs, groups, and channels**.
- **Auto-drafting** runs only in whitelisted chats. Manual `/draft` works anywhere.
- `/fix` is always manual; no "auto-fix" mode.

Out of scope: sending on the user's behalf, message moderation, multi-user deployments, Bot API integration.

## 3. Triggers

Triggering uses Telegram's server-side draft sync (`UpdateDraftMessage`), **not** send-then-delete. The user types a marker into the chat input; the official client syncs the draft to Telegram's servers; we observe the update and react.

### 3.1 `/draft` marker

- User types a message containing `/draft` (anywhere in the text) in the input field of any chat.
- After the official client syncs the draft (typically sub-second after the user pauses), `UpdateDraftMessage` fires.
- We strip the `/draft` token from the draft text. The remainder (anything before and/or after the marker, concatenated) is treated as an optional **user instruction** appended to the system prompt for this generation.
- The drafting pipeline runs (enrichment → LLM) and the generated reply overwrites the chat's draft via `SaveDraftRequest`.

Examples:
- Draft text `/draft` → no extra instruction; generate a reply from context alone.
- Draft text `/draft tell them I can't make it tomorrow` → instruction: "tell them I can't make it tomorrow".
- Draft text `keep it short /draft and ask about the invoice` → instruction: "keep it short and ask about the invoice".

### 3.2 `/fix` marker

- User types text containing `/fix` into the input field of any chat.
- We strip `/fix`. The remainder is the text to correct.
- A separate correction pipeline runs (no chat history, no enrichment, dedicated system prompt).
- The corrected text overwrites the draft.

If the remainder after stripping `/fix` is empty or whitespace, we ignore the trigger.

### 3.3 Auto-draft in whitelisted chats

- On `UpdateNewMessage` for an incoming message in a chat listed under `[drafting].auto_draft_chats`, the drafting pipeline runs automatically (no marker required).
- The generated reply is written as the chat's draft.
- The user's own outgoing messages never trigger auto-draft.

### 3.4 Loop protection

We keep `last_processed_draft[chat_id]` in memory. A draft-update is processed only when both hold:

1. The current draft text differs from the last one we wrote for that chat.
2. The current draft text contains an unprocessed marker.

After writing our generated output, we set `last_processed_draft[chat_id]` to what we wrote so the resulting `UpdateDraftMessage` (echoing our own write) does not retrigger.

## 4. Architecture

### 4.1 Top level

Single-process Python 3.12 asyncio application. Components communicate by direct function call within the event loop.

### 4.2 Components

- **`telegram_client`** — Telethon client; subscribes to `UpdateDraftMessage` and `UpdateNewMessage`; exposes `write_draft(chat_id, text)` and `fetch_history(chat_id, n)`.
- **`trigger_parser`** — given a draft text, identifies which marker (if any) applies, strips it, returns `(kind, remainder)` where `kind ∈ {draft, fix, none}`.
- **`chat_policy`** — given a chat event, decides the action: `skip | auto_draft | manual_draft | manual_fix`. Consumes config + loop-protection state.
- **`context_fetcher`** — calls `telegram_client.fetch_history(chat_id, last_n)`, returns a list of `(sender, timestamp, text)` tuples.
- **`enricher`** — POSTs `{chat_id, messages[]}` to the configured endpoint; returns a context blob (string) or empty on failure/timeout.
- **`drafter`** — Pydantic AI agent. Inputs: resolved system prompt, enrichment blob, chat history, optional user instruction. Output: draft reply string.
- **`corrector`** — Pydantic AI agent. Inputs: correction system prompt, text to rewrite. Output: corrected string.
- **`config_loader`** — loads `config.toml`; watches the file for changes and hot-reloads (via `watchfiles`).
- **`app`** — wires components and runs the asyncio loop.

### 4.3 Data flow — `/draft`

```
UpdateDraftMessage ──► trigger_parser ──► chat_policy (manual_draft)
                                                │
                                      context_fetcher (last N)
                                                │
                                      enricher (HTTP, best-effort)
                                                │
                                      drafter (Pydantic AI)
                                                │
                                      telegram_client.write_draft
```

### 4.4 Data flow — `/fix`

```
UpdateDraftMessage ──► trigger_parser ──► chat_policy (manual_fix)
                                                │
                                      corrector (Pydantic AI)
                                                │
                                      telegram_client.write_draft
```

### 4.5 Data flow — auto-draft

```
UpdateNewMessage ──► chat_policy (auto_draft, whitelist)
                                │
                      context_fetcher → enricher → drafter → write_draft
```

## 5. Configuration

All configuration lives in `config.toml`. No database.

```toml
[telegram]
api_id = 0
api_hash = ""
session = "assistant"   # Telethon session file name

[llm]
model = "anthropic:claude-sonnet-4-6"
timeout_s = 30

[enrichment]
url = "https://example.internal/context"
auth_header = "Bearer ..."   # optional
timeout_s = 10

[markers]
draft = "/draft"
fix = "/fix"

[drafting]
default_system_prompt = """You are drafting a reply in the user's voice. Casual, concise, matches the conversation's tone."""
last_n = 20
auto_draft_chats = []   # list of chat IDs (ints)

[correcting]
system_prompt = """Rewrite the given text correcting grammar, spelling, and punctuation. Preserve meaning, tone, and language. Output only the rewritten text."""

# Optional per-chat overrides. Keyed by chat ID.
# Positive IDs may use bare keys; negative IDs (supergroups/channels) must be quoted.
[chats.12345]
system_prompt = "..."
last_n = 50

[chats."-1001234567890"]
system_prompt = "..."
```

### 5.1 Resolution rules

- System prompt for a chat: `chats.<id>.system_prompt` if set, else `drafting.default_system_prompt`.
- `last_n` for a chat: `chats.<id>.last_n` if set, else `drafting.last_n`.
- Auto-draft: chat ID is in `drafting.auto_draft_chats`.

### 5.2 Hot reload

`watchfiles` observes `config.toml`. On change, `config_loader` reloads and atomically swaps the in-memory config. Running generations keep the old config; new events use the new one.

### 5.3 Secrets

`api_id`, `api_hash`, `llm` credentials (via env vars referenced by Pydantic AI), and `enrichment.auth_header` are sensitive. `config.toml` is git-ignored. A `config.example.toml` is checked in.

## 6. External enrichment endpoint

### 6.1 Request

`POST <enrichment.url>` with header `Authorization: <auth_header>` if configured.

```json
{
  "chat_id": 12345,
  "chat_title": "Alice",
  "messages": [
    {"sender": "Alice", "timestamp": "2026-04-19T10:12:00Z", "text": "..."},
    {"sender": "me",    "timestamp": "2026-04-19T10:13:00Z", "text": "..."}
  ]
}
```

### 6.2 Response

```json
{ "context": "string — may include calendar entries, email snippets, notes, or be empty" }
```

The endpoint decides internally which sources to query and how to format the blob. The assistant does not parse or structure the blob — it is concatenated into the LLM prompt.

### 6.3 Failure handling (best-effort)

- Timeout after `enrichment.timeout_s` → proceed with empty context; log a warning.
- Non-2xx response → proceed with empty context; log a warning.
- Network error → proceed with empty context; log a warning.
- An outage of the enrichment endpoint must never block drafting.

## 7. LLM invocation (Pydantic AI)

### 7.1 Drafter prompt shape

```
System:
<resolved system prompt>

{context_blob, if non-empty, wrapped with a header like "External context:"}

User:
Conversation so far (most recent last):
[sender, ts] text
[sender, ts] text
...

{User instruction from the /draft remainder, if non-empty, wrapped with header "Extra instruction:"}

Draft a reply.
```

### 7.2 Corrector prompt shape

```
System:
<correcting.system_prompt>

User:
<text to rewrite>
```

### 7.3 Timeouts

Each LLM call is wrapped in `asyncio.wait_for(..., llm.timeout_s)`. On timeout: no draft is written, warning logged.

## 8. Error handling

| Failure                                  | Behaviour                                                             |
|------------------------------------------|-----------------------------------------------------------------------|
| LLM timeout or exception                 | No draft written. User's current draft untouched. Warning logged.     |
| Enrichment failure                       | Empty context; drafting continues.                                    |
| Telethon disconnect                      | Telethon auto-reconnects; we re-subscribe to updates.                 |
| Malformed draft (binary, very long text) | Length-capped at 16KB input; silently skipped beyond.                 |
| Config reload with invalid TOML          | Keep previous in-memory config; log error.                            |
| Writing a draft fails                    | Log; do not retry (the user will see the trigger still in the input). |

## 9. Testing

- **Unit tests** (no network, no LLM):
  - `trigger_parser`: draft/fix markers in various positions, empty remainder, no marker, multiple markers (first wins).
  - `chat_policy`: whitelist logic, loop protection, auto vs. manual classification.
  - `enricher`: mock HTTP with `aioresponses`; timeout, non-2xx, network error, happy path.
  - `drafter` / `corrector`: use Pydantic AI's `TestModel` — no real API calls.
  - `config_loader`: valid TOML, invalid TOML, per-chat overrides, reload semantics.
- **Integration tests**:
  - Fake Telethon client fixture that emits `UpdateDraftMessage` / `UpdateNewMessage` events; assert `SaveDraftRequest` is called with expected content.
  - End-to-end `/draft` flow, `/fix` flow, and auto-draft flow.
- **CI**: no real Telegram or LLM credentials; every external dependency mocked.

## 10. Stack

- Python 3.12
- `uv` for packaging and environment management
- `telethon` — MTProto client
- `pydantic-ai` — LLM abstraction with pluggable provider
- `aiohttp` — enrichment HTTP client
- `watchfiles` — config hot-reload
- `tomllib` (stdlib) — config parsing
- `typer` — (optional) small CLI for first-run login and config validation
- `pytest` + `pytest-asyncio` + `aioresponses` — test stack

No SQLite, no database, no persistent state other than `config.toml` and Telethon's own session file.

## 11. Non-goals and tradeoffs

- **Draft-sync latency:** `UpdateDraftMessage` fires after the official client debounces. The user sometimes needs to pause briefly after typing the marker before the assistant reacts. Accepted as a minor UX cost in exchange for avoiding the send-then-delete race.
- **Outgoing self-messages are never replied to automatically.** Only inbound messages trigger auto-draft.
- **No multi-turn agent loop.** Each draft is a single LLM call. If the user wants refinement, they delete our draft, retype with an instruction, and retrigger.
- **No queue, no worker pool.** One inflight generation per chat at a time; re-triggering while one is running cancels the previous.
- **No chat history storage.** We fetch last-N live from Telegram each time. If Telegram is slow, drafting is slow. The in-memory `last_processed_draft` cache (loop protection) is the only runtime state and is discarded on restart.

## 12. Open items for implementation plan

- Precise shape of the chat-history payload sent to the enrichment endpoint (sender identifier: username vs. display name vs. Telegram ID).
- Exact behaviour when multiple markers appear in one draft (spec says "first wins"; plan should codify the ordering rule).
- Log format and destination (stdout vs. file).
- Packaging target: single-command start (`uv run telegram-assistant`) vs. systemd unit example.
