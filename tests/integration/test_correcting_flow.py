from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def test_fix_marker_end_to_end(tmp_path: Path):
    tg = FakeTelegramClient()
    http = aiohttp.ClientSession()
    modules_cfg = {"correcting": {"enabled": True, "system_prompt": "fix", "markers": {}}}
    app = App(tg=tg, llm=fake_llm("CORRECTED"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_draft_update(DraftUpdate(chat_id=7, text="/fix hi how ar you"))
    await app.drain()
    assert tg.drafts[7] == "CORRECTED"
    await app.stop()
    await http.close()
