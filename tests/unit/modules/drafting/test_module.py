from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.events import DraftUpdate, IncomingMessage
from telegram_assistant.markers import MarkerMatch
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.drafting.module import DraftingModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


def _module_config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "enabled": True,
        "default_system_prompt": "SP",
        "last_n": 3,
        "auto_draft_chats": [],
        "enrichment_url": "",
        "enrichment_auth_header": "",
        "enrichment_timeout_s": 5,
        "markers": {"draft": "/draft", "auto_on": "/auto on", "auto_off": "/auto off"},
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
    assert names == {"draft", "auto_on", "auto_off"}
    assert triggers == {"/draft", "/auto on", "/auto off"}
    await ctx.http.close()


async def test_markers_respect_user_overrides(tmp_path: Path):
    mod = DraftingModule()
    ctx, _, _ = await _ctx(
        tmp_path,
        _module_config(markers={"draft": "!d", "auto_on": "!on", "auto_off": "!off"}),
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
    match = _find_marker(mod, "auto_on")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto on"), match)
    assert state.for_module("drafting").get("auto_draft", "1", default=None) is True
    assert tg.drafts[1].startswith("✓ Auto-draft enabled")
    await ctx.http.close()


async def test_auto_off_sets_state_and_confirms(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    await mod.init(ctx)
    match = _find_marker(mod, "auto_off")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto off"), match)
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


def _find_marker(mod: DraftingModule, name: str) -> MarkerMatch:
    for m in mod.markers():
        if m.name == name:
            return MarkerMatch(module="drafting", marker=m, remainder="")
    raise AssertionError(f"no marker {name}")
