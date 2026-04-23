from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

from telegram_assistant.events import DraftUpdate, Message, OutgoingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.correcting.module import CorrectingModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def _ctx(
    tmp_path: Path, markers: dict[str, str] | None = None
) -> tuple[ModuleContext, FakeTelegramClient, RuntimeState]:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    config = {
        "enabled": True,
        "system_prompt": "fix grammar",
        "markers": markers or {},
    }
    ctx = ModuleContext(
        tg=tg,
        llm=fake_llm("CORRECTED"),
        http=http,
        config=config,
        state=state.for_module("correcting"),
        log=logging.getLogger("c"),
    )
    return ctx, tg, state


def _marker(mod: CorrectingModule, name: str) -> Marker:
    for m in mod.markers():
        if m.name == name:
            return m
    raise AssertionError(f"no marker {name}")


async def test_default_markers(tmp_path: Path):
    mod = CorrectingModule()
    ctx, _, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {
        "/fix",
        "/auto_fix on", "/auto_fix off",
        "/auto_fix_sent on", "/auto_fix_sent off",
    }
    await ctx.http.close()


async def test_custom_markers(tmp_path: Path):
    mod = CorrectingModule()
    ctx, _, _ = await _ctx(
        tmp_path,
        markers={
            "fix": "!fix",
            "auto_fix_on": "!af on", "auto_fix_off": "!af off",
            "auto_fix_sent_on": "!afs on", "auto_fix_sent_off": "!afs off",
        },
    )
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"!fix", "!af on", "!af off", "!afs on", "!afs off"}
    await ctx.http.close()


async def test_fix_rewrites_remainder(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    match = MarkerMatch(module="correcting", marker=_marker(mod, "fix"), remainder="hi how ar you")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/fix hi how ar you"), match)
    assert tg.drafts[5] == "CORRECTED"
    await ctx.http.close()


async def test_fix_empty_remainder_ignored(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    match = MarkerMatch(module="correcting", marker=_marker(mod, "fix"), remainder="")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/fix"), match)
    assert tg.drafts == {}
    await ctx.http.close()


async def test_auto_fix_on_sets_state_and_confirms(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    await mod.init(ctx)
    match = MarkerMatch(module="correcting", marker=_marker(mod, "auto_fix_on"), remainder="")
    await mod.on_draft_update(DraftUpdate(chat_id=3, text="/auto_fix on"), match)
    assert state.for_module("correcting").get("auto_fix", "3", default=None) is True
    assert tg.drafts[3].startswith("✓ Auto-fix enabled")
    await ctx.http.close()


async def test_auto_fix_off_sets_state_and_confirms(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    await mod.init(ctx)
    state.for_module("correcting").set("auto_fix", "3", True)
    match = MarkerMatch(module="correcting", marker=_marker(mod, "auto_fix_off"), remainder="")
    await mod.on_draft_update(DraftUpdate(chat_id=3, text="/auto_fix off"), match)
    assert state.for_module("correcting").get("auto_fix", "3", default=None) is False
    assert tg.drafts[3].startswith("✓ Auto-fix disabled")
    await ctx.http.close()


async def test_auto_fix_sent_on_sets_state_and_confirms(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    await mod.init(ctx)
    match = MarkerMatch(
        module="correcting", marker=_marker(mod, "auto_fix_sent_on"), remainder=""
    )
    await mod.on_draft_update(DraftUpdate(chat_id=3, text="/auto_fix_sent on"), match)
    assert state.for_module("correcting").get("auto_fix_sent", "3", default=None) is True
    assert tg.drafts[3].startswith("✓ Auto-fix-sent enabled")
    await ctx.http.close()


async def test_plain_draft_autofix_when_on(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix", "7", True)
    await mod.init(ctx)
    await mod.on_plain_draft_update(DraftUpdate(chat_id=7, text="hi how ar you"))
    assert tg.drafts[7] == "CORRECTED"
    await ctx.http.close()


async def test_plain_draft_skipped_when_off(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    await mod.on_plain_draft_update(DraftUpdate(chat_id=7, text="hi how ar you"))
    assert tg.drafts == {}
    await ctx.http.close()


async def test_plain_draft_empty_text_skipped(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix", "7", True)
    await mod.init(ctx)
    await mod.on_plain_draft_update(DraftUpdate(chat_id=7, text="  "))
    assert tg.drafts == {}
    await ctx.http.close()


async def test_plain_draft_unchanged_text_does_not_overwrite(tmp_path: Path):
    # If the LLM returns the same text as the input, we skip the write —
    # avoids logging an "overwrite with identical text" event.
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix", "7", True)
    # Swap the llm's canned response to match the input exactly.
    ctx = ModuleContext(
        tg=ctx.tg, llm=fake_llm("already correct"), http=ctx.http,
        config=ctx.config, state=ctx.state, log=ctx.log,
    )
    await mod.init(ctx)
    await mod.on_plain_draft_update(DraftUpdate(chat_id=7, text="already correct"))
    assert tg.drafts == {}
    await ctx.http.close()


def _outgoing(chat_id: int, text: str, message_id: int = 42) -> OutgoingMessage:
    msg = Message(
        chat_id=chat_id,
        message_id=message_id,
        sender="me",
        timestamp=datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc),
        text=text,
        outgoing=True,
    )
    return OutgoingMessage(msg)


async def test_outgoing_edit_when_autofixsent_on(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix_sent", "9", True)
    await mod.init(ctx)
    await mod.on_outgoing_message(_outgoing(9, "hi how ar you", message_id=42))
    assert len(tg.edits) == 1
    assert tg.edits[0].chat_id == 9
    assert tg.edits[0].message_id == 42
    assert tg.edits[0].text == "CORRECTED"
    await ctx.http.close()


async def test_outgoing_skipped_when_autofixsent_off(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    await mod.on_outgoing_message(_outgoing(9, "hi how ar you"))
    assert tg.edits == []
    await ctx.http.close()


async def test_outgoing_skipped_when_empty(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix_sent", "9", True)
    await mod.init(ctx)
    await mod.on_outgoing_message(_outgoing(9, "   "))
    assert tg.edits == []
    await ctx.http.close()


async def test_outgoing_skipped_when_corrected_equals_original(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix_sent", "9", True)
    ctx = ModuleContext(
        tg=ctx.tg, llm=fake_llm("already correct"), http=ctx.http,
        config=ctx.config, state=ctx.state, log=ctx.log,
    )
    await mod.init(ctx)
    await mod.on_outgoing_message(_outgoing(9, "already correct"))
    assert tg.edits == []
    await ctx.http.close()


async def test_outgoing_fix_marker_edits_without_autofixsent(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    # auto_fix_sent is NOT on — but /fix in the sent text should still trigger an edit.
    await mod.on_outgoing_message(_outgoing(9, "/fix hi how ar you", message_id=50))
    assert len(tg.edits) == 1
    assert tg.edits[0].chat_id == 9
    assert tg.edits[0].message_id == 50
    assert tg.edits[0].text == "CORRECTED"
    await ctx.http.close()


async def test_outgoing_fix_marker_strips_remainder_correctly(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    await mod.on_outgoing_message(
        _outgoing(9, "please /fix thsi is a tset", message_id=51)
    )
    # Remainder is "please thsi is a tset" — the LLM returns "CORRECTED" for any input.
    assert tg.edits[0].text == "CORRECTED"
    await ctx.http.close()


async def test_outgoing_fix_marker_wins_over_autofixsent(tmp_path: Path):
    # When both paths would activate, /fix takes precedence — single edit fired.
    mod = CorrectingModule()
    ctx, tg, state = await _ctx(tmp_path)
    state.for_module("correcting").set("auto_fix_sent", "9", True)
    await mod.init(ctx)
    await mod.on_outgoing_message(_outgoing(9, "/fix hi how ar you", message_id=52))
    assert len(tg.edits) == 1
    await ctx.http.close()


async def test_outgoing_fix_marker_empty_remainder_no_edit(tmp_path: Path):
    mod = CorrectingModule()
    ctx, tg, _ = await _ctx(tmp_path)
    await mod.init(ctx)
    # "/fix" alone — remainder empty, nothing to correct.
    await mod.on_outgoing_message(_outgoing(9, "/fix", message_id=53))
    assert tg.edits == []
    await ctx.http.close()
