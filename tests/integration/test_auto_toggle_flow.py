"""Per-chat auto features are driven by state.toml / Toggle, not in-chat markers."""
from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate, IncomingMessage
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


def _toggle(app: App, key: str):
    from telegram_assistant.app import _Services

    [t] = [t for t in _Services(app).toggles() if t.key == key]
    return t


async def test_state_toggle_drives_auto_draft(tmp_path: Path):
    tg = FakeTelegramClient()
    tg.seed_history(9, [make_message(9, "alice", "hi")])
    http = aiohttp.ClientSession()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [],
            "auto_draft_debounce_s": 0,
            "markers": {},
        }
    }
    state_path = tmp_path / "state.toml"
    state_path.write_text('[drafting.auto_draft]\n"9" = true\n')
    app = App(tg=tg, llm=fake_llm("R"), http=http, state_path=state_path)
    await app.start(modules_cfg)

    # state.toml enables it: an incoming message is auto-drafted.
    await app.inject_incoming(IncomingMessage(make_message(9, "alice", "hi")))
    await app.drain()
    assert tg.drafts[9] == "R"

    # The settings-menu Toggle turns it off; the next message is ignored.
    del tg.drafts[9]
    _toggle(app, "drafting.auto_draft").set(9, False)
    await app.inject_incoming(IncomingMessage(make_message(9, "alice", "again")))
    await app.drain()
    assert 9 not in tg.drafts

    await app.stop()
    await http.close()


async def test_removed_toggle_markers_are_plain_text(tmp_path: Path):
    tg = FakeTelegramClient()
    http = aiohttp.ClientSession()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [],
            "markers": {},
        },
        "correcting": {"enabled": True, "system_prompt": "fix", "context_last_n": 0},
    }
    state_path = tmp_path / "state.toml"
    app = App(tg=tg, llm=fake_llm("R"), http=http, state_path=state_path)
    await app.start(modules_cfg)

    for text in ("/auto_draft on", "/auto_fix on", "/auto_fix_sent on"):
        await app.inject_draft_update(DraftUpdate(chat_id=9, text=text))
    await app.drain()
    assert tg.drafts == {}
    assert not state_path.exists() or "auto_" not in state_path.read_text()

    await app.stop()
    await http.close()
