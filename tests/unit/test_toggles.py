"""Modules expose toggles bound to their own effective-value logic."""
from __future__ import annotations

import logging
from pathlib import Path

import aiohttp

from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.correcting.module import CorrectingModule
from telegram_assistant.modules.drafting.module import DraftingModule
from telegram_assistant.modules.media_reply.module import MediaReplyModule
from telegram_assistant.state import RuntimeState
from telegram_assistant.toggles import chat_ids
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def _init(mod, tmp_path: Path, config: dict, http):
    state = RuntimeState(tmp_path / "state.toml")
    ctx = ModuleContext(
        tg=FakeTelegramClient(), llm=fake_llm("X"), http=http, config=config,
        state=state.for_module(mod.name), log=logging.getLogger(mod.name),
    )
    await mod.init(ctx)
    return state


def test_chat_ids_skips_junk():
    assert chat_ids(["5", "-1001", "x"]) == {5, -1001}


async def test_correcting_toggles(tmp_path: Path):
    async with aiohttp.ClientSession() as http:
        mod = CorrectingModule()
        state = await _init(mod, tmp_path, {"system_prompt": "x"}, http)
        fix, fix_sent = mod.toggles()
        assert (fix.key, fix.label) == ("correcting.auto_fix", "Korrektur vor dem Senden")
        assert (fix_sent.key, fix_sent.label) == ("correcting.auto_fix_sent", "Korrektur nach dem Senden")
        assert not fix.is_on(5)
        fix.set(5, True)
        assert fix.is_on(5) and mod._is_on(5, "auto_fix")
        assert not fix_sent.is_on(5)
        assert state.for_module("correcting").get("auto_fix", "5", None) is True
        assert fix.chats() == {5} and fix_sent.chats() == set()


async def test_drafting_toggle_state_overrides_seed(tmp_path: Path):
    async with aiohttp.ClientSession() as http:
        mod = DraftingModule()
        await _init(mod, tmp_path, {
            "default_system_prompt": "x", "last_n": 5, "auto_draft_chats": [7],
        }, http)
        [t] = mod.toggles()
        assert (t.key, t.label) == ("drafting.auto_draft", "Antwortvorschläge")
        assert t.is_on(7) and t.chats() == {7}
        t.set(7, False)
        assert not t.is_on(7) and not mod._auto_on(7)
        t.set(9, True)
        assert t.chats() == {7, 9}


async def test_media_reply_toggle_state_overrides_seed(tmp_path: Path):
    async with aiohttp.ClientSession() as http:
        mod = MediaReplyModule()
        await _init(mod, tmp_path, {"chats": [-1001]}, http)
        [t] = mod.toggles()
        assert (t.key, t.label) == ("media_reply.auto_media", "Medien-Antworten")
        assert t.is_on(-1001)
        t.set(-1001, False)
        assert not mod._auto_on(-1001)
