from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import IncomingMessage
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


async def test_auto_draft_end_to_end(tmp_path: Path):
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    http = aiohttp.ClientSession()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [42],
            "enrichment_url": "",
            "markers": {},
        }
    }
    app = App(tg=tg, llm=fake_llm("HELLO BACK"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_incoming(IncomingMessage(make_message(42, "alice", "hi")))
    await app.drain()
    assert tg.drafts[42] == "HELLO BACK"
    await app.stop()
    await http.close()
