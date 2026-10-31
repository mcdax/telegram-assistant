"""End-to-end: settings-menu buttons drive the real modules' per-chat state."""
from __future__ import annotations

import tomllib
from pathlib import Path

import aiohttp
import pytest

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate, IncomingMessage, OutgoingMessage
from telegram_assistant.modules.settings_menu.module import SettingsMenuModule
from tests.fakes.bot_api import (
    OWNER,
    FakeBotApi,
    callback,
    chat_shared,
    owner_text,
    users_shared,
)
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message

GROUP = -1001234567890  # Telethon event.chat_id == Bot API id for a supergroup


def _modules_cfg(*, media: bool = False) -> dict:
    cfg = {
        "agent": {
            "enabled": False,
            "bot_token_env": "AGENT_BOT_TOKEN",
            "target_chat_id": OWNER,
        },
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [5],
            "auto_draft_debounce_s": 0,
            "markers": {},
        },
        "correcting": {"enabled": True, "system_prompt": "fix", "context_last_n": 0},
        "settings_menu": {"enabled": True},
    }
    if media:
        cfg["media_reply"] = {"enabled": True, "chats": [], "handlers": []}
    return cfg


@pytest.fixture
async def setup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "123:abc")
    api = FakeBotApi()
    monkeypatch.setattr(SettingsMenuModule, "_api_override", lambda _http, _tok: api)
    tg = FakeTelegramClient()
    http = aiohttp.ClientSession()
    app = App(tg=tg, llm=fake_llm("FIXED"), http=http, state_path=tmp_path / "state.toml")
    yield app, api, tg, tmp_path / "state.toml"
    await app.stop()
    await http.close()


def _button_texts(markup: dict) -> list[str]:
    return [row[0]["text"] for row in markup["inline_keyboard"]]


async def test_menu_shows_only_loaded_modules(setup):
    app, api, _, _ = setup
    await app.start(_modules_cfg())
    api.push(users_shared(5, "Mara"))
    [send] = await api.wait_for("sendMessage")
    # drafting seed has chat 5 → on; media_reply not loaded → no button.
    assert _button_texts(send["reply_markup"]) == [
        "❌ Korrektur vor dem Senden",
        "❌ Korrektur nach dem Senden",
        "✅ Antwortvorschläge",
    ]


async def test_menu_includes_media_when_loaded(setup):
    app, api, _, _ = setup
    await app.start(_modules_cfg(media=True))
    api.push(users_shared(5, "Mara"))
    [send] = await api.wait_for("sendMessage")
    assert _button_texts(send["reply_markup"])[-1] == "❌ Medien-Antworten"


async def test_button_toggle_drives_autofix_in_supergroup(setup):
    app, api, tg, state_path = setup
    await app.start(_modules_cfg())

    # Before: auto_fix off → plain draft untouched.
    await app.inject_draft_update(DraftUpdate(chat_id=GROUP, text="helo"))
    await app.drain()
    assert GROUP not in tg.drafts

    api.push(chat_shared(GROUP, "Team"))
    await api.wait_for("sendMessage")
    api.push(callback(f"t:{GROUP}:correcting.auto_fix"))
    [ans] = await api.wait_for("answerCallbackQuery")
    assert ans["text"] == "Korrektur vor dem Senden: an"
    [edit] = await api.wait_for("editMessageReplyMarkup")
    assert _button_texts(edit["reply_markup"])[0] == "✅ Korrektur vor dem Senden"

    # Persisted in the same place the in-chat marker writes.
    data = tomllib.loads(state_path.read_text())
    assert data["correcting"]["auto_fix"][str(GROUP)] is True

    # After: the module sees it immediately.
    await app.inject_draft_update(DraftUpdate(chat_id=GROUP, text="helo"))
    await app.drain()
    assert tg.drafts[GROUP] == "FIXED"


async def test_button_off_overrides_drafting_seed(setup):
    app, api, tg, _ = setup
    tg.seed_history(5, [make_message(5, "mara", "hi")])
    await app.start(_modules_cfg())

    api.push(callback("t:5:drafting.auto_draft"))
    [ans] = await api.wait_for("answerCallbackQuery")
    assert ans["text"] == "Antwortvorschläge: aus"

    await app.inject_incoming(IncomingMessage(make_message(5, "mara", "hi")))
    await app.drain()
    assert 5 not in tg.drafts


async def test_in_chat_marker_and_button_stay_consistent(setup):
    app, api, tg, _ = setup
    await app.start(_modules_cfg())

    # In-chat marker turns auto_fix_sent on …
    await app.inject_draft_update(DraftUpdate(chat_id=8, text="/auto_fix_sent on"))
    await app.drain()
    assert tg.drafts[8].startswith("✓ Auto-fix-sent enabled")

    # … the menu shows it …
    api.push(users_shared(8, "Bob"))
    [send] = await api.wait_for("sendMessage")
    assert _button_texts(send["reply_markup"])[1] == "✅ Korrektur nach dem Senden"

    # … and the overview lists it.
    api.push(owner_text("📋 Übersicht"))
    sends = await api.wait_for("sendMessage", count=2)
    assert "• Bob: Korrektur nach dem Senden" in sends[1]["text"]

    # Button turns it off → post-send fix no longer edits.
    api.push(callback("t:8:correcting.auto_fix_sent"))
    await api.wait_for("answerCallbackQuery")
    await app.inject_outgoing(OutgoingMessage(make_message(8, "me", "helo", message_id=3, outgoing=True)))
    await app.drain()
    assert tg.edits == []


async def test_non_owner_cannot_toggle(setup):
    app, api, _, state_path = setup
    await app.start(_modules_cfg())
    api.push(callback("t:9:correcting.auto_fix", from_id=1234))
    api.push(owner_text("/settings"))
    await api.wait_for("sendMessage")
    assert api.of("answerCallbackQuery") == []
    assert not state_path.exists()
