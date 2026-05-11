from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


async def test_agent_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    tg.drafts[42] = "/agent summarize"
    http = aiohttp.ClientSession()

    sent: list[dict] = []

    async def fake_send(http_, token, *, chat_id: int, text: str) -> None:
        sent.append({"token": token, "chat_id": chat_id, "text": text})

    # The agent module's default sender is captured at AgentModule construction
    # time, which happens inside ``app.start``. Patch the module attribute
    # BEFORE start() so the constructor picks up our fake.
    import telegram_assistant.modules.agent.module as agent_module
    monkeypatch.setattr(agent_module._bot_sender, "send_text", fake_send)

    modules_cfg = {
        "agent": {
            "enabled": True,
            "debounce_s": 0.05,
            "last_n": 5,
            "default_system_prompt": "",
            "bot_token_env": "AGENT_BOT_TOKEN",
            "target_chat_id": 7777,
            "markers": {},
        },
    }
    app = App(tg=tg, llm=fake_llm("AGENT REPLY"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent summarize"))
    await app.drain()
    # Allow the debounce timer to elapse and the run to complete.
    await asyncio.sleep(0.3)

    assert tg.drafts[42] == ""
    assert sent == [{"token": "tok", "chat_id": 7777, "text": "AGENT REPLY"}]
    await app.stop()
    await http.close()
