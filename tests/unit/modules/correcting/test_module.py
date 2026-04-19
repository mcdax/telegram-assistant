from __future__ import annotations

import logging
from pathlib import Path

import aiohttp

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.correcting.module import CorrectingModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def _ctx(tmp_path: Path, user_trigger: str | None = None) -> ModuleContext:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    markers = {"fix": user_trigger} if user_trigger else {}
    config = {
        "enabled": True,
        "system_prompt": "fix grammar",
        "markers": markers,
    }
    return ModuleContext(
        tg=tg,
        llm=fake_llm("CORRECTED"),
        http=http,
        config=config,
        state=state.for_module("correcting"),
        log=logging.getLogger("c"),
    )


async def test_default_marker(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path)
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"/fix"}
    await ctx.http.close()


async def test_custom_marker(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path, user_trigger="!fix")
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"!fix"}
    await ctx.http.close()


async def test_fix_rewrites_remainder(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path)
    await mod.init(ctx)
    m = mod.markers()[0]
    match = MarkerMatch(module="correcting", marker=m, remainder="hi how ar you")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/fix hi how ar you"), match)
    assert ctx.tg.drafts[5] == "CORRECTED"  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_empty_remainder_ignored(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path)
    await mod.init(ctx)
    m = mod.markers()[0]
    match = MarkerMatch(module="correcting", marker=m, remainder="")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/fix"), match)
    assert ctx.tg.drafts == {}  # type: ignore[attr-defined]
    await ctx.http.close()
