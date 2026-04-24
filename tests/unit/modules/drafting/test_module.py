from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.events import DraftUpdate, IncomingMessage, MessageEdited, OutgoingMessage
from telegram_assistant.markers import MarkerMatch
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.drafting.module import DraftingModule
from telegram_assistant.state import RuntimeState, StateWriteError
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


def _module_config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "enabled": True,
        "default_system_prompt": "SP",
        "last_n": 3,
        "auto_draft_chats": [],
        "markers": {"draft": "/draft", "auto_draft_on": "/auto_draft on", "auto_draft_off": "/auto_draft off"},
    }
    base.update(overrides)
    return base


async def _ctx(tmp_path: Path, config: dict[str, Any]) -> tuple[ModuleContext, FakeTelegramClient, RuntimeState]:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    ctx = ModuleContext(
        tg=tg,
        llm=fake_llm("GENERATED"),
        http=http,
        config=config,
        state=state.for_module("drafting"),
        log=logging.getLogger("drafting"),
    )
    return ctx, tg, state


async def test_markers_use_defaults():
    mod = DraftingModule()
    ctx, _, _ = await _ctx(Path("/tmp"), _module_config())
    await mod.init(ctx)
    names = {m.name for m in mod.markers()}
    triggers = {m.trigger for m in mod.markers()}
    assert names == {"draft", "auto_draft_on", "auto_draft_off"}
    assert triggers == {"/draft", "/auto_draft on", "/auto_draft off"}
    await ctx.http.close()


async def test_markers_respect_user_overrides(tmp_path: Path):
    mod = DraftingModule()
    ctx, _, _ = await _ctx(
        tmp_path,
        _module_config(markers={"draft": "!d", "auto_draft_on": "!on", "auto_draft_off": "!off"}),
    )
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"!d", "!on", "!off"}
    await ctx.http.close()


async def test_on_incoming_skips_outgoing(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="me", text="x", outgoing=True))
    )
    assert tg.drafts == {}
    await ctx.http.close()


async def test_on_incoming_auto_draft_whitelisted(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_on_incoming_skips_when_not_whitelisted(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config(auto_draft_chats=[]))
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts == {}
    await ctx.http.close()


async def test_runtime_state_overrides_seed_to_off(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    state.for_module("drafting").set("auto_draft", "1", False)
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts == {}
    await ctx.http.close()


async def test_runtime_state_overrides_seed_to_on(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[]))
    state.for_module("drafting").set("auto_draft", "1", True)
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_auto_on_sets_state_and_confirms(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config())
    await mod.init(ctx)
    match = _find_marker(mod, "auto_draft_on")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto_draft on"), match)
    assert state.for_module("drafting").get("auto_draft", "1", default=None) is True
    assert tg.drafts[1].startswith("✓ Auto-draft enabled")
    await ctx.http.close()


async def test_auto_off_sets_state_and_confirms(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    await mod.init(ctx)
    match = _find_marker(mod, "auto_draft_off")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto_draft off"), match)
    assert state.for_module("drafting").get("auto_draft", "1", default=None) is False
    assert tg.drafts[1].startswith("✓ Auto-draft disabled")
    await ctx.http.close()


async def test_draft_marker_runs_pipeline(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config())
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)
    match = _find_marker(mod, "draft")
    # Simulate a resolved marker with a remainder
    from telegram_assistant.markers import MarkerMatch

    match_with_remainder = MarkerMatch(module="drafting", marker=match.marker, remainder="ask X")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/draft ask X"), match_with_remainder)
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_set_auto_writes_failure_draft_on_state_write_error(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config())
    await mod.init(ctx)
    match = _find_marker(mod, "auto_draft_on")
    with patch("os.replace", side_effect=OSError("disk full")):
        await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto_draft on"), match)
    assert tg.drafts[1] == "✗ state write failed"
    await ctx.http.close()


def _find_marker(mod: DraftingModule, name: str) -> MarkerMatch:
    for m in mod.markers():
        if m.name == name:
            return MarkerMatch(module="drafting", marker=m, remainder="")
    raise AssertionError(f"no marker {name}")


# ---------- debounce + edit behaviour ----------


async def test_second_incoming_within_window_is_debounced(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    # First message drafts immediately.
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    assert tg.drafts[1] == "GENERATED"

    # Reset the fake-tg draft state so we can see whether a second draft lands.
    tg.drafts.clear()

    # Second incoming in the 60s window must NOT draft immediately — it
    # schedules a debounced task. No new draft written synchronously.
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    assert tg.drafts == {}
    assert 1 in mod._pending  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_debounced_task_cancels_on_next_trigger(tmp_path: Path):
    import asyncio

    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    first_task = mod._pending[1]  # type: ignore[attr-defined]

    # Third trigger cancels the previously pending task and schedules a new one.
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi3")))
    # Let the cancellation propagate through the event loop.
    await asyncio.sleep(0)
    assert first_task.cancelled() or first_task.done()
    assert 1 in mod._pending  # type: ignore[attr-defined]
    assert mod._pending[1] is not first_task  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_user_send_cancels_pending_and_clears_cooldown(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    assert 1 in mod._pending  # type: ignore[attr-defined]

    outgoing = make_message(1, "me", "ok thanks", outgoing=True)
    await mod.on_outgoing_message(OutgoingMessage(outgoing))

    # Pending is cancelled and state cleared, so the NEXT incoming drafts
    # immediately again rather than being debounced.
    assert 1 not in mod._pending  # type: ignore[attr-defined]
    assert 1 not in mod._last_drafted_at  # type: ignore[attr-defined]

    tg.drafts.clear()
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi3")))
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_debounce_disabled_drafts_every_message(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=0),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    assert tg.drafts[1] == "GENERATED"
    tg.drafts.clear()
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    assert tg.drafts[1] == "GENERATED"
    assert 1 not in mod._pending  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_edit_of_incoming_triggers_same_debounce(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    # Edit with no prior cooldown → drafts immediately.
    await mod.on_message_edited(MessageEdited(make_message(1, "alice", "hi edited")))
    assert tg.drafts[1] == "GENERATED"

    # Edit within cooldown → deferred.
    tg.drafts.clear()
    await mod.on_message_edited(MessageEdited(make_message(1, "alice", "hi edited again")))
    assert tg.drafts == {}
    assert 1 in mod._pending  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_edit_respects_auto_draft_off(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[], auto_draft_debounce_s=60),
    )
    await mod.init(ctx)
    await mod.on_message_edited(MessageEdited(make_message(1, "alice", "hi edited")))
    assert tg.drafts == {}
    assert 1 not in mod._pending  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_auto_draft_skipped_when_user_is_drafting(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    # User is mid-composing a reply.
    await mod.on_plain_draft_update(DraftUpdate(chat_id=1, text="I'm writing..."))

    # Incoming arrives; normally would auto-draft immediately (idle).
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    assert tg.drafts == {}
    await ctx.http.close()


async def test_auto_draft_resumes_after_user_clears_draft(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_plain_draft_update(DraftUpdate(chat_id=1, text="I'm writing..."))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    assert tg.drafts == {}

    # User clears their input.
    await mod.on_plain_draft_update(DraftUpdate(chat_id=1, text=""))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi again")))
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_user_drafting_cancels_pending_debounced_task(tmp_path: Path):
    import asyncio

    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    # First incoming drafts immediately; second schedules debounce.
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    pending = mod._pending[1]  # type: ignore[attr-defined]
    assert not pending.done()

    # User starts typing — pending task must be cancelled.
    await mod.on_plain_draft_update(DraftUpdate(chat_id=1, text="no thanks"))
    await asyncio.sleep(0)
    assert pending.cancelled() or pending.done()
    assert 1 not in mod._pending  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_outgoing_clears_user_drafting(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_plain_draft_update(DraftUpdate(chat_id=1, text="a reply"))
    outgoing = make_message(1, "me", "a reply", outgoing=True)
    await mod.on_outgoing_message(OutgoingMessage(outgoing))

    # Next incoming should proceed to auto-draft (user has sent, no
    # longer drafting).
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "thanks")))
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_openai_drafter_is_used_when_configured(tmp_path: Path, monkeypatch):
    """When [modules.drafting.openai] is fully set, the OpenAI drafter is
    built and its output reaches the draft slot instead of the default path."""
    import json

    from telegram_assistant.modules.drafting.openai_drafter import OpenAIDrafter
    from tests.fakes.llm import fake_llm

    monkeypatch.setenv("TEST_OPENAI_KEY", "sk-abc")

    cfg = _module_config(
        auto_draft_chats=[1],
        openai={
            "base_url": "https://api.example/v1",
            "api_key_env": "TEST_OPENAI_KEY",
            "model": "m1",
            "instruction": "be concise",
        },
    )
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, cfg)
    tg.seed_history(1, [make_message(1, "alice", "hi", sender_id=777)])
    await mod.init(ctx)
    assert mod._openai_drafter is not None  # type: ignore[attr-defined]

    # Replace the drafter's factory with a capturing one so we don't hit
    # the real OpenAIChatModel HTTP transport. The instruction passed in
    # via the config block is preserved on the drafter instance.
    captured: dict[str, str] = {}

    class _CapturingFactory:
        def agent(self, system_prompt: str):
            captured["system_prompt"] = system_prompt
            return "agent-sentinel"

        async def run(self, agent, user_text: str) -> str:  # noqa: ARG002
            captured["user_text"] = user_text
            return "OPENAI OUTPUT"

    mod._openai_drafter = OpenAIDrafter(  # type: ignore[attr-defined]
        factory=_CapturingFactory(),  # type: ignore[arg-type]
        instruction="be concise",
    )

    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))

    assert tg.drafts[1] == "OPENAI OUTPUT"
    assert captured["system_prompt"] == ""  # no system-role message
    # User text = instruction line, blank line, then JSON payload.
    assert captured["user_text"].startswith("be concise\n\n")
    payload = json.loads(captured["user_text"].split("\n\n", 1)[1])
    assert payload["chat_id"] == 1
    assert [m["is_me"] for m in payload["messages"]] == [False]
    await ctx.http.close()


async def test_openai_drafter_not_built_when_config_missing(tmp_path: Path):
    mod = DraftingModule()
    ctx, _, _ = await _ctx(tmp_path, _module_config())
    await mod.init(ctx)
    assert mod._openai_drafter is None  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_shutdown_cancels_pending_tasks(tmp_path: Path):
    import asyncio

    mod = DraftingModule()
    ctx, tg, _ = await _ctx(
        tmp_path, _module_config(auto_draft_chats=[1], auto_draft_debounce_s=60),
    )
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)

    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi")))
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "hi2")))
    task = mod._pending[1]  # type: ignore[attr-defined]

    await mod.shutdown()
    # The shutdown fires cancel() on the task; let the loop deliver it.
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled() or task.done()
    await ctx.http.close()
