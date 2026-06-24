# Post-send `/draft` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a sent message containing the `/draft` marker get replaced in place by a generated reply, mirroring `/fix-in-sent`.

**Architecture:** Extend `drafting.on_outgoing_message` to detect the `/draft` marker (it currently only does auto-draft cooldown cleanup). On a match, generate a reply from history + the instruction remainder and edit the sent message via the Telegram edit API instead of writing the draft. A single optional `message_id` parameter on the existing `_draft()` method selects edit-vs-write and excludes the just-sent message from history.

**Tech Stack:** Python 3.12+, `pytest` (`asyncio_mode = "auto"`), `uv`, in-memory `FakeTelegramClient` / `RecordingLLM` test fakes.

## Global Constraints

- Python 3.12+; imports are `telegram_assistant.*` (`pythonpath = ["src"]`).
- Async tests need no `@pytest.mark.asyncio` (auto mode).
- Commit as `mcdax`; do NOT pass `-c user.email`/`user.name` overrides.
- No new config key; `/draft` marker is already registered.
- Treat `asyncio.CancelledError` as a normal abort; never write partial output after cancellation (existing pattern, unchanged here).
- All work happens on branch `draft-post-send` (already created; spec already committed there).

---

### Task 1: Post-send `/draft` edits the sent message

**Files:**
- Modify: `src/telegram_assistant/modules/drafting/module.py` (`on_outgoing_message` at lines 130-140; `_draft` at lines 261-287; add `_draft_marker` helper)
- Test: `tests/unit/modules/drafting/test_module.py`

**Interfaces:**
- Consumes (existing, unchanged):
  - `self._ctx.tg.fetch_history(chat_id: int, n: int) -> list[Message]`
  - `self._ctx.tg.write_draft(chat_id: int, text: str) -> None`
  - `self._ctx.tg.edit_message(chat_id: int, message_id: int, text: str) -> None`
  - `Marker.match(text: str) -> tuple[bool, str | None]`
  - `OutgoingMessage.message` is a `Message` with `.chat_id`, `.message_id`, `.text`
- Produces:
  - `_draft(self, *, chat_id: int, chat_title: str, instruction: str, message_id: int | None = None) -> None` — when `message_id is None`, writes the draft (existing behaviour); when set, excludes that message from history and edits it in place.
  - `_draft_marker(self) -> Marker | None` — returns the registered `draft` marker.

- [ ] **Step 1: Write the failing tests**

Add these imports near the top of `tests/unit/modules/drafting/test_module.py` (extend the existing `tests.fakes.telegram` import to include `EditedMessage`, and the `tests.fakes.llm` import to include `RecordingLLM`):

```python
from tests.fakes.telegram import EditedMessage, FakeTelegramClient, make_message
from tests.fakes.llm import RecordingLLM, fake_llm
```

Append these tests at the end of the file:

```python
# ---------- post-send /draft (edit sent message in place) ----------


async def test_post_send_draft_edits_sent_message(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config())
    tg.seed_history(1, [make_message(1, "alice", "hi", message_id=10)])
    await mod.init(ctx)

    sent = make_message(
        1, "me", "/draft ask about the meeting", message_id=20, outgoing=True
    )
    await mod.on_outgoing_message(OutgoingMessage(sent))

    assert tg.edits == [EditedMessage(chat_id=1, message_id=20, text="GENERATED")]
    assert tg.drafts == {}
    await ctx.http.close()


async def test_post_send_draft_excludes_own_message_from_history(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config())
    # Swap in a recording LLM so we can inspect the prompt the pipeline built.
    ctx.llm = RecordingLLM("GENERATED")
    tg.seed_history(
        1,
        [
            make_message(1, "alice", "INCOMING_CONTEXT", message_id=10),
            make_message(1, "me", "/draft reply now", message_id=20, outgoing=True),
        ],
    )
    await mod.init(ctx)

    sent = make_message(1, "me", "/draft reply now", message_id=20, outgoing=True)
    await mod.on_outgoing_message(OutgoingMessage(sent))

    assert len(ctx.llm.calls) == 1  # type: ignore[attr-defined]
    prompt = ctx.llm.calls[0]  # type: ignore[attr-defined]
    assert "INCOMING_CONTEXT" in prompt          # context kept
    assert "/draft reply now" not in prompt      # own sent message excluded
    await ctx.http.close()


async def test_post_send_draft_empty_instruction_still_drafts(tmp_path: Path):
    # Unlike /fix, a bare /draft (empty instruction) is valid: draft from
    # history alone and still edit (which strips the marker).
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config())
    tg.seed_history(1, [make_message(1, "alice", "hi", message_id=10)])
    await mod.init(ctx)

    sent = make_message(1, "me", "/draft", message_id=20, outgoing=True)
    await mod.on_outgoing_message(OutgoingMessage(sent))

    assert tg.edits == [EditedMessage(chat_id=1, message_id=20, text="GENERATED")]
    await ctx.http.close()


async def test_normal_outgoing_does_not_edit(tmp_path: Path):
    # A sent message without the marker leaves cooldown cleanup intact and
    # never calls the edit API.
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60)
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    assert 1 in mod._pending  # type: ignore[attr-defined]

    sent = make_message(1, "me", "ok thanks", message_id=20, outgoing=True)
    await mod.on_outgoing_message(OutgoingMessage(sent))

    assert tg.edits == []
    assert 1 not in mod._pending  # type: ignore[attr-defined]
    await ctx.http.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/modules/drafting/test_module.py -k post_send_draft -v`
Expected: FAIL — `test_post_send_draft_edits_sent_message` asserts `tg.edits == [...]` but `on_outgoing_message` does not edit yet, so `tg.edits == []`.

- [ ] **Step 3: Add the `_draft_marker` helper**

In `src/telegram_assistant/modules/drafting/module.py`, add this method (place it just above `_trigger_auto_draft`, around line 177):

```python
    def _draft_marker(self) -> Marker | None:
        for m in self._markers:
            if m.name == "draft":
                return m
        return None
```

- [ ] **Step 4: Detect the marker in `on_outgoing_message`**

Replace the current `on_outgoing_message` (lines 130-140) with:

```python
    async def on_outgoing_message(self, event: OutgoingMessage) -> None:
        """Post-send /draft, then auto-draft bookkeeping.

        If the sent text contains the /draft marker, replace the sent message
        in place with a generated reply (mirrors /fix-in-sent) and return.
        Otherwise our pending debounce is moot once the user sends: cancel it
        and clear cooldown so the next inbound activity drafts fresh.
        """
        assert self._ctx is not None
        msg = event.message
        draft_marker = self._draft_marker()
        matched, remainder = draft_marker.match(msg.text) if draft_marker else (False, None)
        if matched:
            self._ctx.log.debug(
                "post-send /draft chat=%s id=%s remainder=%r",
                msg.chat_id, msg.message_id, (remainder or "")[:80],
            )
            await self._draft(
                chat_id=msg.chat_id,
                chat_title="",
                instruction=remainder or "",
                message_id=msg.message_id,
            )
            return
        chat_id = msg.chat_id
        if self._cancel_pending(chat_id):
            self._ctx.log.debug("outgoing in chat=%s cancelled pending debounce", chat_id)
        if chat_id in self._last_drafted_at:
            self._last_drafted_at.pop(chat_id, None)
            self._ctx.log.debug("outgoing in chat=%s cleared cooldown", chat_id)
        # User sent → any prior in-progress draft is consumed.
        self._user_drafting.pop(chat_id, None)
```

- [ ] **Step 5: Add the `message_id` parameter to `_draft`**

Replace `_draft` (lines 261-287) with:

```python
    async def _draft(
        self,
        *,
        chat_id: int,
        chat_title: str,
        instruction: str,
        message_id: int | None = None,
    ) -> None:
        assert self._ctx is not None
        system_prompt, last_n = self._resolve_for_chat(chat_id)
        self._ctx.log.debug(
            "draft chat=%s last_n=%d instruction=%r system_prompt_len=%d message_id=%s",
            chat_id, last_n, instruction[:80], len(system_prompt), message_id,
        )
        history = await self._ctx.tg.fetch_history(chat_id, last_n)
        if message_id is not None:
            history = [m for m in history if m.message_id != message_id]
        self._ctx.log.debug("fetched history chat=%s messages=%d", chat_id, len(history))
        try:
            if self._openai_drafter is not None:
                output = await self._openai_drafter.draft(
                    chat_id=chat_id,
                    chat_title=chat_title,
                    history=history,
                    instruction=instruction,
                )
            else:
                pipeline = Pipeline(llm=self._ctx.llm, system_prompt=system_prompt)
                output = await pipeline.run(
                    enrichment="", history=history, instruction=instruction
                )
        except Exception as e:
            self._ctx.log.warning("drafting failed chat=%s: %s", chat_id, e)
            return
        self._ctx.log.debug("draft generated chat=%s len=%d", chat_id, len(output))
        if message_id is not None:
            await self._ctx.tg.edit_message(chat_id, message_id, output)
        else:
            await self._ctx.tg.write_draft(chat_id, output)
```

- [ ] **Step 6: Run the new tests to verify they pass**

Run: `uv run pytest tests/unit/modules/drafting/test_module.py -k post_send_draft -v`
Expected: PASS (all four new tests; `test_normal_outgoing_does_not_edit` matches `-k post_send_draft`? No — run the next command to be sure).

Run: `uv run pytest tests/unit/modules/drafting/test_module.py -v`
Expected: PASS — all tests in the file, including `test_normal_outgoing_does_not_edit` and every pre-existing test, pass.

- [ ] **Step 7: Run the full suite + lint**

Run: `uv run pytest && uv run ruff check`
Expected: PASS — no regressions, no lint errors.

- [ ] **Step 8: Commit**

```bash
git add src/telegram_assistant/modules/drafting/module.py tests/unit/modules/drafting/test_module.py
git commit -m "feat(drafting): post-send /draft edits the sent message in place

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Documentation

**Files:**
- Modify: `src/telegram_assistant/modules/drafting/module.py` (module docstring, lines 1-11)
- Modify: `CLAUDE.md` (the `drafting` module bullet under "Modules")

**Interfaces:**
- Consumes: nothing (docs only).
- Produces: nothing.

- [ ] **Step 1: Update the module docstring**

In `src/telegram_assistant/modules/drafting/module.py`, replace the first docstring line (line 1) so it documents the post-send path. Change:

```python
"""Drafting module. Owns /draft, /auto_draft on, /auto_draft off markers and auto-draft policy.
```

to:

```python
"""Drafting module. Owns /draft, /auto_draft on, /auto_draft off markers and auto-draft policy.

/draft seen in a draft overwrites the draft input with a generated reply.
/draft seen in an already-sent message (the user pressed Send with the marker)
edits that message in place with the generated reply (mirrors /fix-in-sent);
the just-sent message is excluded from the history context. Unlike /fix, an
empty instruction is valid and drafts from history alone.
```

(Leave the rest of the existing docstring — the "Auto-draft debouncing" section — unchanged.)

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, find the `drafting` bullet under the "### Modules" section. Replace:

```
- `drafting` — `/draft` (one-shot) + `/auto_draft on|off` (per-chat). Debounced auto-draft with cooldown; tracks whether the user is mid-typing (`on_plain_draft_update`) to avoid clobbering their input. Optional OpenAI-compatible backend via `[modules.drafting.openai]` (`openai_drafter.py`), else the `pydantic-ai` `Pipeline`.
```

with:

```
- `drafting` — `/draft` (one-shot, overwrites the draft; if the marker is sent, edits the sent message in place like `/fix-in-sent`) + `/auto_draft on|off` (per-chat). Debounced auto-draft with cooldown; tracks whether the user is mid-typing (`on_plain_draft_update`) to avoid clobbering their input. Optional OpenAI-compatible backend via `[modules.drafting.openai]` (`openai_drafter.py`), else the `pydantic-ai` `Pipeline`.
```

- [ ] **Step 3: Verify nothing broke**

Run: `uv run pytest tests/unit/modules/drafting/test_module.py -q`
Expected: PASS (docstring/markdown edits don't affect behaviour; this is a sanity check).

- [ ] **Step 4: Commit**

```bash
git add src/telegram_assistant/modules/drafting/module.py CLAUDE.md
git commit -m "docs(drafting): document post-send /draft behaviour

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Post-send `/draft` edits sent message → Task 1 (Steps 4-5, `test_post_send_draft_edits_sent_message`). ✓
- Exclude just-sent message from history → Task 1 (Step 5, `test_post_send_draft_excludes_own_message_from_history`). ✓
- Empty instruction still drafts (differs from `/fix`) → Task 1 (`test_post_send_draft_empty_instruction_still_drafts`). ✓
- LLM error → no edit → covered by the unchanged `try/except` in `_draft` (returns before the edit/write branch). ✓
- Normal send leaves cooldown logic intact → Task 1 (`test_normal_outgoing_does_not_edit`). ✓
- No `/auto_draft_sent`, no new config key → not implemented (by design). ✓
- Docstring + CLAUDE.md → Task 2. ✓

**Placeholder scan:** No TBD/TODO/"handle edge cases"; all steps contain concrete code and exact commands. ✓

**Type consistency:** `_draft(..., message_id: int | None = None)`, `_draft_marker() -> Marker | None`, and `Marker.match -> tuple[bool, str | None]` are used consistently across `on_outgoing_message` and `_draft`. `EditedMessage(chat_id, message_id, text)` matches the fake in `tests/fakes/telegram.py`. `RecordingLLM(response)` matches `tests/fakes/llm.py`. ✓
