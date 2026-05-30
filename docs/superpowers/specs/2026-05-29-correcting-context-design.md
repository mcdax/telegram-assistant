# Correcting module: chat-history context — design

**Date:** 2026-05-29
**Status:** Approved (pending spec review)
**Module:** `src/telegram_assistant/modules/correcting/`

## Problem

The correcting module (`/fix`, `/auto_fix`, `/auto_fix_sent`) sends only the
text to be corrected to the LLM, with no surrounding conversation. The drafting
and agent modules both fetch the last N chat messages to ground the LLM. The
correcting flows should do the same so corrections can disambiguate proper
nouns, terminology, and the message's language from the conversation — without
otherwise changing what the user wrote.

## Decisions (from brainstorming)

- **Scope:** all three flows — `/fix`, `auto_fix` (pre-send), `auto_fix_sent`
  (post-send).
- **Context use:** disambiguation only. Corrections stay strictly
  grammar/spelling/punctuation; word choice, tone, and content are unchanged.
  Context exists solely to resolve names/terms/language.
- **Amount:** default **5** previous messages, configurable, `0` disables.
- **Delivery:** Approach A — an inline, clearly delimited context block inside
  the user message. The global `LLMFactory` (`system_prompt` + `user_text`)
  is unchanged; no multi-turn `message_history`, no reuse of the drafting
  `OpenAIDrafter` machinery.

## Behaviour & data flow

All three flows already funnel through one method, `_correct(text)`. Context
logic is centralised there:

```
async def _correct(self, chat_id: int, text: str, *, exclude_message_id: int | None = None) -> str | None
```

1. Fetch context: `history = ctx.tg.fetch_history(chat_id, context_last_n)`.
2. If `exclude_message_id` is set, drop the message with that id from history.
3. Build the user content (see below) and run the existing global LLM agent
   built from the configured `system_prompt`.

Per-flow wiring (all call sites already know `chat_id`):

| Flow | Target text | History exclusion |
|---|---|---|
| `/fix` in a **draft** (`_fix_remainder`) | draft remainder | none — draft is not a message |
| `auto_fix` pre-send (`on_plain_draft_update`) | draft text | none |
| `/fix` in a **sent** message (`_fix_sent_via_marker`) | remainder after marker | `exclude_message_id = message_id` |
| `auto_fix_sent` post-send (`on_outgoing_message`) | sent text | `exclude_message_id = msg.message_id` |

The exclusion prevents the just-sent message from being both context and
target.

### Graceful degradation (feature is additive and disable-able)

- `context_last_n = 0` → no fetch; send only the target text (today's exact
  payload).
- Empty history or a `fetch_history` exception → fall back to the no-context
  payload; never fail the correction because context could not be gathered.

## Prompt construction

New config key `[modules.correcting].context_last_n` (default `5`). The
user-configured `system_prompt` is **unchanged**, so already-deployed configs
keep working.

`_build_user_content(history, text)` — a **pure function** (no I/O), so it is
directly unit-testable:

- **Empty history** → returns `text` verbatim (byte-identical to today's
  payload; guarantees backward compatibility).
- **Non-empty history** → returns:

  ```
  Recent conversation (context only — do not correct or reply to these):
  Me: …
  Alice: …

  Text to correct (output only the corrected version of this):
  <text>
  ```

- Sender label: `Me` when `message.outgoing`, else `message.sender`.
- Non-text messages render with their short `attachment.description` (falling
  back to the message text when absent).

The authoritative "how to correct" instruction stays in `system_prompt`; the
inline labels add structure and a disambiguation-only reminder (belt and
suspenders against the model rewriting content or replying to the context).

## Config

`config.example.toml`, `[modules.correcting]` block, gains:

```toml
# Number of preceding chat messages sent as context to disambiguate names,
# terms, and language during correction. Context never changes the meaning of
# your text. 0 disables context entirely.
context_last_n = 5
```

Read in `init` as `int(cfg.get("context_last_n", 5))`.

## Testing

The existing `fake_llm` (pydantic-ai `TestModel`) returns a canned string and
does **not** record its input, so payload assertions need either a pure
function or a recording fake.

- **`_build_user_content` unit tests (primary):** empty history → verbatim
  passthrough; non-empty → context block present, correct `Me`/sender labels,
  target text under the "Text to correct" label, attachment description used
  for media messages.
- **Recording LLM fake:** add a small recording variant to `tests/fakes/llm.py`
  that captures the last `user_text` passed to `run`, to assert end-to-end
  through `_correct` that context reaches the LLM and that the target text is
  preserved.
- **Flow tests** (via `FakeTelegramClient.seed_history`):
  - `auto_fix_sent` / `/fix`-in-sent exclude the message being corrected.
  - `context_last_n = 0` sends no context.
  - `fetch_history` raising → correction still proceeds with no context.

## Out of scope

- Multi-turn `message_history` delivery (Approach B).
- Tone/formality adaptation from context (explicitly rejected — disambiguation
  only).
- Changes to the drafting/agent context mechanisms.
</content>
