from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate, IncomingMessage
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


async def test_auto_on_then_incoming_drafts(tmp_path: Path):
    tg = FakeTelegramClient()
    tg.seed_history(9, [make_message(9, "alice", "hi")])
    http = aiohttp.ClientSession()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [],
            "markers": {},
        }
    }
    app = App(tg=tg, llm=fake_llm("R"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)

    # First: /auto_draft on flips state, writes confirmation.
    await app.inject_draft_update(DraftUpdate(chat_id=9, text="/auto_draft on"))
    await app.drain()
    assert tg.drafts[9].startswith("✓ Auto-draft enabled")

    # Then: an incoming message is auto-drafted.
    await app.inject_incoming(IncomingMessage(make_message(9, "alice", "hi")))
    await app.drain()
    assert tg.drafts[9] == "R"

    # Finally: /auto_draft off disables.
    await app.inject_draft_update(DraftUpdate(chat_id=9, text="/auto_draft off"))
    await app.drain()
    assert tg.drafts[9].startswith("✓ Auto-draft disabled")

    await app.stop()
    await http.close()
