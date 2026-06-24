# Post-send `/draft` — design

**Date:** 2026-06-24
**Module:** `drafting` (`src/telegram_assistant/modules/drafting/`)

## Goal

Make `/draft` work after a message is sent, mirroring `/fix-in-sent`. When the
user sends a message containing the `/draft` marker, the bot generates a reply
draft from the conversation (instruction + history) and **replaces the sent
message in place** via Telegram's edit API.

Today `/draft` only fills the draft input field (`write_draft`). Pressing Send
too fast lets the marker escape as a normal message and it is ignored. After
this change, that sent `/draft …` message gets turned into the generated reply.

## Scope

- Only the **explicit `/draft` marker** post-send. No `/auto_draft_sent` toggle
  (rewriting every sent message into an AI reply has no sensible use; YAGNI).
- No new config key.

## Behaviour

User sends `"/draft frag nach dem Termin"`:

1. Telegram fires `OutgoingMessage` (the message now has a `message_id`).
2. `drafting.on_outgoing_message` detects the `/draft` marker.
3. History is fetched and the just-sent message is excluded.
4. LLM generates a reply from history + the instruction remainder (existing
   OpenAI-backend path or `Pipeline`, unchanged).
5. `edit_message(chat_id, message_id, output)` replaces the sent message.

### Difference from `/fix-in-sent`

`/fix` ignores an empty remainder (nothing to correct). `/draft` treats an
**empty instruction as valid** — it drafts from history alone, exactly like the
existing auto-draft and bare `/draft` paths. So post-send `/draft` must NOT bail
on an empty instruction; it always generates and always edits (which also strips
the marker from the visible message).

## Design

Minimal-diff approach, mirroring the `exclude_message_id` pattern already used in
`correcting._correct`. All changes are in `modules/drafting/module.py`.

### 1. `_draft()` gains an optional `message_id`

```python
async def _draft(
    self, *, chat_id: int, chat_title: str, instruction: str,
    message_id: int | None = None,
) -> None:
    ...
    history = await self._ctx.tg.fetch_history(chat_id, last_n)
    if message_id is not None:
        history = [m for m in history if m.message_id != message_id]
    ... # LLM call unchanged
    if message_id is not None:
        await self._ctx.tg.edit_message(chat_id, message_id, output)
    else:
        await self._ctx.tg.write_draft(chat_id, output)
```

`message_id is None` → existing behaviour (fill the draft). `message_id` set →
exclude that message from history and edit it in place. The chosen alternative
(extracting a separate `_generate()` method) was rejected: more code for the same
effect with only two callers.

### 2. `_draft_marker()` helper

Pendant to `correcting._fix_marker()` — returns the registered `draft` `Marker`
(or `None`), so `on_outgoing_message` can match against the sent text.

### 3. `on_outgoing_message` detects the marker first

```python
async def on_outgoing_message(self, event: OutgoingMessage) -> None:
    msg = event.message
    draft_marker = self._draft_marker()
    matched, remainder = draft_marker.match(msg.text) if draft_marker else (False, None)
    if matched:
        await self._draft(
            chat_id=msg.chat_id, chat_title="",
            instruction=remainder or "", message_id=msg.message_id,
        )
        return
    # existing cooldown / pending-debounce cleanup for normal sends
    ...
```

Early-return mirrors `correcting.on_outgoing_message`. A `/draft` marker send is
a command, not a real reply, so it skips the auto-draft cooldown bookkeeping —
acceptable and consistent with the `/fix` path.

## Data flow

`OutgoingMessage` → marker matched → fetch history & exclude own `message_id` →
LLM (OpenAI path or `Pipeline`, unchanged) → `edit_message(chat_id, message_id, output)`.

## Error handling

- LLM error/timeout → logged, no edit (existing `try/except` in `_draft`); the
  sent message stays unchanged.
- Empty instruction → still drafts from history and edits (strips the marker).
- `CONTAINS` match risk (a message coincidentally containing `/draft`) → identical
  to `/fix`'s existing risk, accepted.

## Testing

`tests/unit/modules/drafting/`:

- Post-send `/draft` edits the sent message with the generated reply
  (`edit_message` called with the LLM output).
- The just-sent message is excluded from the history passed to the LLM.
- Empty instruction (`/draft` alone, sent) still drafts and edits (does **not**
  bail like `/fix`).
- A normal outgoing message (no marker) leaves the existing cooldown / pending
  cleanup behaviour unchanged (no `edit_message`).

## Docs

- Update the `drafting` module docstring to document the post-send `/draft` path
  (mirror the `correcting` module docstring style).
- Update the `drafting` module description in `CLAUDE.md`.
- No `config.example.toml` change (no new config key).
