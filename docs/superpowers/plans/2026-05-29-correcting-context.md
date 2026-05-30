# Correcting-Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send the last N chat messages as disambiguation-only context to all three correcting flows (`/fix`, `auto_fix`, `auto_fix_sent`).

**Architecture:** Centralise context handling in the correcting module's single `_correct` choke point. A pure formatter builds a clearly delimited context block inside the user message; the global `LLMFactory` (`system_prompt` + `user_text`) is unchanged. Context is additive and disabled by `context_last_n = 0`; missing/failed history degrades to today's no-context payload.

**Tech Stack:** Python 3.12, pytest (`asyncio_mode = auto`), pydantic-ai `TestModel` fakes, `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-05-29-correcting-context-design.md`

---

## File Structure

- `src/telegram_assistant/modules/correcting/module.py` — **modify.** Add two module-level pure functions (`_render_message`, `_build_user_content`), a `_fetch_context` method, a `context_last_n` config read, the new `_correct` signature, and update the four call sites.
- `tests/fakes/llm.py` — **modify.** Add a `RecordingLLM` that captures `user_text` passed to `run`.
- `tests/unit/modules/correcting/test_module.py` — **modify.** Add formatter, config, and flow tests.
- `config.example.toml` — **modify.** Document `context_last_n` under `[modules.correcting]`.

---

### Task 1: Pure history formatter

**Files:**
- Modify: `src/telegram_assistant/modules/correcting/module.py` (imports near line 13; add functions near the `DEFAULT_MARKERS` block)
- Test: `tests/unit/modules/correcting/test_module.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/modules/correcting/test_module.py`. Add this import near the top imports (the file already imports from `telegram_assistant.events`):

```python
from telegram_assistant.events import Attachment
from telegram_assistant.modules.correcting.module import (
    _build_user_content,
    _render_message,
)
from tests.fakes.telegram import make_message
```

Then add the tests:

```python
def test_render_message_incoming_and_outgoing():
    incoming = make_message(chat_id=1, sender="Alice", text="hallo")
    outgoing = make_message(chat_id=1, sender="Me", text="hi", outgoing=True)
    assert _render_message(incoming) == "Alice: hallo"
    assert _render_message(outgoing) == "Me: hi"


def test_render_message_media_uses_attachment_description():
    msg = make_message(
        chat_id=1,
        sender="Alice",
        text="",
        message_type="voice",
        attachment=Attachment(type="voice", description="voice 12s", url=None),
    )
    assert _render_message(msg) == "Alice: voice 12s"


def test_build_user_content_empty_history_is_verbatim():
    assert _build_user_content([], "fix me") == "fix me"


def test_build_user_content_wraps_with_context_block():
    history = [
        make_message(chat_id=1, sender="Alice", text="see you at the Kö"),
        make_message(chat_id=1, sender="Me", text="ok", outgoing=True),
    ]
    out = _build_user_content(history, "ill be their soon")
    assert "Recent conversation (context only" in out
    assert "Alice: see you at the Kö" in out
    assert "Me: ok" in out
    assert out.rstrip().endswith("ill be their soon")
    assert "Text to correct" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/modules/correcting/test_module.py -k "render_message or build_user_content" -v`
Expected: FAIL with `ImportError: cannot import name '_build_user_content'`.

- [ ] **Step 3: Add `Message` to the events import**

In `module.py`, change the existing import line:

```python
from telegram_assistant.events import DraftUpdate, OutgoingMessage
```

to:

```python
from telegram_assistant.events import DraftUpdate, Message, OutgoingMessage
```

- [ ] **Step 4: Implement the formatter functions**

In `module.py`, immediately after the `_AUTO_FIX_SENT_BUCKET = "auto_fix_sent"` line (before `class CorrectingModule`), add:

```python
DEFAULT_CONTEXT_LAST_N = 5


def _render_message(m: Message) -> str:
    """One context line: 'Me: …' for our messages, else 'Sender: …'.

    Media-only messages (no text) render their attachment description.
    """
    label = "Me" if m.outgoing else m.sender
    body = m.text.strip()
    if not body and m.attachment is not None:
        body = m.attachment.description
    return f"{label}: {body}"


def _build_user_content(history: list[Message], text: str) -> str:
    """Wrap the text to correct with a delimited, context-only history block.

    Empty history → the text verbatim (byte-identical to the no-context
    payload), keeping behaviour unchanged when context is off/unavailable.
    """
    if not history:
        return text
    lines = "\n".join(_render_message(m) for m in history)
    return (
        "Recent conversation (context only — do not correct or reply to these):\n"
        f"{lines}\n\n"
        "Text to correct (output only the corrected version of this):\n"
        f"{text}"
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/modules/correcting/test_module.py -k "render_message or build_user_content" -v`
Expected: PASS (4 tests).

- [ ] **Step 6: Commit**

```bash
git add src/telegram_assistant/modules/correcting/module.py tests/unit/modules/correcting/test_module.py
git commit -m "feat(correcting): pure history-context formatter"
```

---

### Task 2: `context_last_n` config + `_fetch_context` helper

**Files:**
- Modify: `src/telegram_assistant/modules/correcting/module.py` (`__init__` ~line 34, `init` ~line 38)
- Test: `tests/unit/modules/correcting/test_module.py`

- [ ] **Step 1: Write the failing tests**

The existing `_ctx` helper builds `config` without `context_last_n`; tests pass an explicit value through `ctx.config`. Add these tests (they construct the module directly and call the helper):

```python
async def test_context_last_n_defaults_to_5(tmp_path: Path):
    mod = CorrectingModule()
    ctx, _, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    assert mod._context_last_n == 5
    await ctx.http.close()


async def test_context_last_n_read_from_config(tmp_path: Path):
    mod = CorrectingModule()
    ctx, _, _ = await _ctx(tmp_path)
    ctx.config["context_last_n"] = 3
    await mod.init(ctx)
    assert mod._context_last_n == 3
    await ctx.http.close()


async def test_fetch_context_disabled_when_zero(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    ctx.config["context_last_n"] = 0
    await mod.init(ctx)
    tg.seed_history(1, [make_message(chat_id=1, sender="Alice", text="hi")])
    assert await mod._fetch_context(1, None) == []
    await ctx.http.close()


async def test_fetch_context_excludes_message_id(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    tg.seed_history(1, [
        make_message(chat_id=1, sender="Alice", text="hi", message_id=10),
        make_message(chat_id=1, sender="Me", text="hey", message_id=11, outgoing=True),
    ])
    result = await mod._fetch_context(1, exclude_message_id=11)
    assert [m.message_id for m in result] == [10]
    await ctx.http.close()


async def test_fetch_context_returns_empty_on_fetch_error(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)

    async def boom(chat_id, n):
        raise RuntimeError("telethon down")

    tg.fetch_history = boom  # type: ignore[method-assign]
    assert await mod._fetch_context(1, None) == []
    await ctx.http.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/modules/correcting/test_module.py -k "context_last_n or fetch_context" -v`
Expected: FAIL — `AttributeError: 'CorrectingModule' object has no attribute '_context_last_n'`.

- [ ] **Step 3: Initialise `_context_last_n` in `__init__`**

In `module.py`, the current `__init__` is:

```python
    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []
```

Add one line:

```python
    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []
        self._context_last_n: int = DEFAULT_CONTEXT_LAST_N
```

- [ ] **Step 4: Read the config key in `init`**

In `init`, after `self._ctx = ctx` and before the `def trigger` helper, add:

```python
        self._context_last_n = int(ctx.config.get("context_last_n", DEFAULT_CONTEXT_LAST_N))
```

- [ ] **Step 5: Add the `_fetch_context` method**

In `module.py`, add this method to `CorrectingModule` immediately above `_correct`:

```python
    async def _fetch_context(
        self, chat_id: int, exclude_message_id: int | None
    ) -> list[Message]:
        """Last N messages for disambiguation. Empty when disabled or on error."""
        assert self._ctx is not None
        if self._context_last_n <= 0:
            return []
        try:
            history = await self._ctx.tg.fetch_history(chat_id, self._context_last_n)
        except Exception as e:
            self._ctx.log.debug(
                "context fetch failed chat=%s: %s — correcting without context",
                chat_id, e,
            )
            return []
        if exclude_message_id is not None:
            history = [m for m in history if m.message_id != exclude_message_id]
        return list(history)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/modules/correcting/test_module.py -k "context_last_n or fetch_context" -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add src/telegram_assistant/modules/correcting/module.py tests/unit/modules/correcting/test_module.py
git commit -m "feat(correcting): context_last_n config and history fetch helper"
```

---

### Task 3: Recording LLM fake + wire context into `_correct` and call sites

**Files:**
- Modify: `tests/fakes/llm.py`
- Modify: `src/telegram_assistant/modules/correcting/module.py` (`_correct` ~line 182, and call sites at `_fix_remainder` ~line 169, `on_plain_draft_update` ~line 88, `_fix_sent_via_marker` ~line 141, `on_outgoing_message` ~line 105)
- Test: `tests/unit/modules/correcting/test_module.py`

- [ ] **Step 1: Add the recording LLM fake**

Append to `tests/fakes/llm.py`:

```python
class RecordingLLM(LLMFactory):
    """LLMFactory that records the user_text of every run() call."""

    def __init__(self, response: str, *, timeout_s: int = 5) -> None:
        super().__init__(model=TestModel(custom_output_text=response), timeout_s=timeout_s)
        self.calls: list[str] = []

    async def run(self, agent, user_text: str) -> str:  # type: ignore[override]
        self.calls.append(user_text)
        return await super().run(agent, user_text)
```

- [ ] **Step 2: Write the failing flow tests**

Add the import near the top of `tests/unit/modules/correcting/test_module.py`:

```python
from tests.fakes.llm import RecordingLLM
```

Add a helper and tests. The helper rebuilds a ctx with a `RecordingLLM` (the default `_ctx` uses a non-recording fake):

```python
async def _ctx_recording(tmp_path: Path):
    ctx, tg, state = await _ctx(tmp_path)
    rec = RecordingLLM("CORRECTED")
    ctx = ModuleContext(
        tg=ctx.tg, llm=rec, http=ctx.http, config=ctx.config,
        state=ctx.state, log=ctx.log,
    )
    return ctx, tg, rec


async def test_fix_draft_includes_context(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, rec = await _ctx_recording(tmp_path)
    await mod.init(ctx)
    tg.seed_history(1, [make_message(chat_id=1, sender="Alice", text="meet at the Kö")])
    await mod._fix_remainder(1, "ill be their")
    assert rec.calls, "LLM was not called"
    assert "Alice: meet at the Kö" in rec.calls[0]
    assert rec.calls[0].rstrip().endswith("ill be their")
    assert tg.drafts[1] == "CORRECTED"
    await ctx.http.close()


async def test_fix_without_history_sends_text_only(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, rec = await _ctx_recording(tmp_path)
    await mod.init(ctx)
    await mod._fix_remainder(1, "fix me")
    assert rec.calls == ["fix me"]
    await ctx.http.close()


async def test_auto_fix_sent_excludes_own_message(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, rec = await _ctx_recording(tmp_path)
    ctx.config["context_last_n"] = 5
    await mod.init(ctx)
    # auto_fix_sent on for chat 1
    ctx.state.set(_AUTO_FIX_SENT_BUCKET, "1", True)
    tg.seed_history(1, [
        make_message(chat_id=1, sender="Alice", text="how are you", message_id=20),
        make_message(chat_id=1, sender="Me", text="i'm god", message_id=21, outgoing=True),
    ])
    sent = make_message(chat_id=1, sender="Me", text="i'm god", message_id=21, outgoing=True)
    await mod.on_outgoing_message(OutgoingMessage(sent))
    assert rec.calls, "LLM was not called"
    payload = rec.calls[0]
    assert "Alice: how are you" in payload
    # the message being corrected must not appear in the context block
    assert payload.count("Me: i'm god") == 0
    assert payload.rstrip().endswith("i'm god")
    await ctx.http.close()
```

Note: `_AUTO_FIX_SENT_BUCKET` is already importable — add it to the existing
`from telegram_assistant.modules.correcting.module import ...` line if not
already present (Task 1 imported `_build_user_content`, `_render_message`;
extend that import to include `_AUTO_FIX_SENT_BUCKET`).

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/unit/modules/correcting/test_module.py -k "includes_context or text_only or excludes_own" -v`
Expected: FAIL — `_correct` currently takes `(self, text)`, so `_fix_remainder` calls `_correct(text)`; the recorded payload has no context block (`assert "Alice: meet at the Kö" in ...` fails).

- [ ] **Step 4: Rewrite `_correct` to fetch + wrap context**

Replace the current `_correct`:

```python
    async def _correct(self, text: str) -> str | None:
        assert self._ctx is not None
        agent = self._ctx.llm.agent(self._ctx.config["system_prompt"])
        try:
            return await self._ctx.llm.run(agent, text)
        except Exception as e:
            self._ctx.log.warning("correcting failed: %s", e)
            return None
```

with:

```python
    async def _correct(
        self, chat_id: int, text: str, *, exclude_message_id: int | None = None
    ) -> str | None:
        assert self._ctx is not None
        history = await self._fetch_context(chat_id, exclude_message_id)
        user_content = _build_user_content(history, text)
        agent = self._ctx.llm.agent(self._ctx.config["system_prompt"])
        try:
            return await self._ctx.llm.run(agent, user_content)
        except Exception as e:
            self._ctx.log.warning("correcting failed: %s", e)
            return None
```

- [ ] **Step 5: Update the four call sites**

In `_fix_remainder` — current line:
```python
        corrected = await self._correct(text)
```
becomes:
```python
        corrected = await self._correct(chat_id, text)
```

In `on_plain_draft_update` (auto_fix) — current line:
```python
        corrected = await self._correct(text)
```
becomes:
```python
        corrected = await self._correct(event.chat_id, text)
```

In `_fix_sent_via_marker` — current line:
```python
        corrected = await self._correct(text)
```
becomes:
```python
        corrected = await self._correct(chat_id, text, exclude_message_id=message_id)
```

In `on_outgoing_message` (auto_fix_sent path) — current line:
```python
        corrected = await self._correct(text)
```
becomes:
```python
        corrected = await self._correct(msg.chat_id, text, exclude_message_id=msg.message_id)
```

Note: there are four `await self._correct(text)` call sites with identical
text; edit each in its own method (use the surrounding method context shown
above to target the right one).

- [ ] **Step 6: Run the targeted tests**

Run: `uv run pytest tests/unit/modules/correcting/test_module.py -k "includes_context or text_only or excludes_own" -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the full correcting + integration suite**

Run: `uv run pytest tests/unit/modules/correcting/ tests/integration/test_correcting_flow.py -v`
Expected: PASS (all existing tests still green — the call-site signature change is internal).

- [ ] **Step 8: Commit**

```bash
git add src/telegram_assistant/modules/correcting/module.py tests/fakes/llm.py tests/unit/modules/correcting/test_module.py
git commit -m "feat(correcting): send chat history as disambiguation context"
```

---

### Task 4: Document config key and run the full suite

**Files:**
- Modify: `config.example.toml` (`[modules.correcting]` block, ~line 128)

- [ ] **Step 1: Add the config documentation**

In `config.example.toml`, under `[modules.correcting]`, directly below the
`system_prompt = "..."` line, add:

```toml

# Number of preceding chat messages sent as context to help disambiguate
# names, terms, and language during correction. Context never changes the
# meaning of your text — corrections stay grammar/spelling/punctuation only.
# 0 disables context entirely.
context_last_n = 5
```

- [ ] **Step 2: Run the entire test suite**

Run: `uv run pytest`
Expected: PASS (full suite green).

- [ ] **Step 3: Lint**

Run: `uv run ruff check src/telegram_assistant/modules/correcting/module.py tests/fakes/llm.py`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add config.example.toml
git commit -m "docs(correcting): document context_last_n config key"
```

---

## Self-Review

**Spec coverage:**
- All three flows get context → Task 3 wires all four call sites (`/fix` draft + sent, `auto_fix`, `auto_fix_sent`). ✓
- Disambiguation-only via delimited block, `system_prompt` unchanged → Task 1 `_build_user_content`. ✓
- Default 5, configurable, `0` disables → Task 2 config read + `_fetch_context` guard. ✓
- Exclude the corrected message from context → Task 2/3 `exclude_message_id`. ✓
- Graceful degradation (no/failed history, `0`) → Task 2 `_fetch_context` (empty list → verbatim payload). ✓
- Approach A, global `LLMFactory` unchanged → `_correct` still calls `ctx.llm.agent`/`run`. ✓
- Tests: pure-function unit tests + recording fake + flow tests → Tasks 1–3. ✓
- `config.example.toml` documents the key → Task 4. ✓

**Placeholder scan:** none — every code/test step shows complete code.

**Type/signature consistency:** `_correct(chat_id, text, *, exclude_message_id=None)` defined in Task 3 and used with matching args at all four call sites. `_fetch_context(chat_id, exclude_message_id)` and `_build_user_content(history, text)` / `_render_message(m)` names are consistent across tasks. `DEFAULT_CONTEXT_LAST_N` defined in Task 1, used in Task 2. ✓
</content>
