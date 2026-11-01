"""Tests for the settings menu module against a fake Bot API."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.settings_menu import ui
from telegram_assistant.modules.settings_menu.module import (
    ALLOWED_UPDATES,
    SettingsMenuModule,
)
from telegram_assistant.state import RuntimeState, StateWriteError
from telegram_assistant.toggles import Toggle
from tests.fakes.bot_api import (
    BOT_CHAT,
    OWNER,
    FakeBotApi,
    callback,
    chat_shared,
    owner_text,
    users_shared,
)
from tests.fakes.telegram import FakeTelegramClient


class FakeServices:
    def __init__(self, toggles: list[Toggle], configs: dict[str, dict[str, Any]] | None = None) -> None:
        self._toggles = toggles
        self._configs = configs or {}
        self.handlers: list = []

    def bot_callback_handlers(self) -> list:
        return list(self.handlers)

    def toggles(self) -> list[Toggle]:
        return list(self._toggles)

    def module_config(self, name: str) -> dict[str, Any]:
        return dict(self._configs.get(name, {}))


def _dict_toggle(module: str, bucket: str, label: str, store: dict[int, bool]) -> Toggle:
    return Toggle(
        module=module, bucket=bucket, label=label,
        is_on=lambda c: store.get(c, False),
        set=lambda c, v: store.__setitem__(c, v),
        chats=lambda: set(store),
    )


@pytest.fixture
async def http():
    session = aiohttp.ClientSession()
    yield session
    await session.close()


@pytest.fixture
def api(monkeypatch) -> FakeBotApi:
    fake = FakeBotApi()
    monkeypatch.setattr(SettingsMenuModule, "_api_override", lambda _http, _tok: fake)
    return fake


async def _start(
    tmp_path: Path, http, *, toggles: list[Toggle] | None = None,
    config: dict | None = None, agent_cfg: dict | None = None,
    tg: FakeTelegramClient | None = None,
) -> tuple[SettingsMenuModule, FakeTelegramClient]:
    tg = tg or FakeTelegramClient()
    agent = agent_cfg if agent_cfg is not None else {
        "bot_token_env": "AGENT_BOT_TOKEN", "target_chat_id": OWNER,
    }
    ctx = ModuleContext(
        tg=tg, llm=None, http=http,  # type: ignore[arg-type]
        config={"enabled": True, "retry_initial_s": 0.01, "retry_max_s": 0.04, **(config or {})},
        state=RuntimeState(tmp_path / "state.toml").for_module("settings_menu"),
        log=logging.getLogger("settings_menu"),
        services=FakeServices(toggles or [], {"agent": agent}),
    )
    mod = SettingsMenuModule()
    await mod.init(ctx)
    return mod, tg


# ---------- init ----------

async def test_disabled_without_token(tmp_path, http, api, monkeypatch, caplog):
    monkeypatch.delenv("AGENT_BOT_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING):
        mod, _ = await _start(tmp_path, http)
    assert not mod.active
    assert "menu disabled" in caplog.text
    await asyncio.sleep(0.02)
    assert api.calls == []
    await mod.shutdown()


async def test_disabled_without_owner(tmp_path, http, api, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "1:x")
    mod, _ = await _start(tmp_path, http, agent_cfg={"bot_token_env": "AGENT_BOT_TOKEN", "target_chat_id": -100})
    assert not mod.active
    await mod.shutdown()


async def test_own_config_overrides_agent(tmp_path, http, monkeypatch):
    monkeypatch.setenv("MENU_TOKEN", "9:y")
    seen: list[str] = []

    def factory(_http, token):
        seen.append(token)
        return FakeBotApi()

    monkeypatch.setattr(SettingsMenuModule, "_api_override", factory)
    mod, _ = await _start(
        tmp_path, http, config={"bot_token_env": "MENU_TOKEN", "owner_user_id": 42}, agent_cfg={},
    )
    assert mod.active and seen == ["9:y"] and mod._owner_id == 42
    await mod.shutdown()


async def test_startup_registers_commands_and_polls_only_message_and_callback(
    tmp_path, http, api, monkeypatch,
):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "1:x")
    mod, _ = await _start(tmp_path, http)
    polls = await api.wait_for("getUpdates")
    assert api.calls[0][0] == "setMyCommands"
    assert api.calls[0][1]["commands"] == [{"command": "settings", "description": "Einstellungen pro Chat"}]
    assert polls[0]["allowed_updates"] == ALLOWED_UPDATES == ["message", "callback_query"]
    await mod.shutdown()
    assert mod._task is None


async def test_poll_backs_off_on_errors_and_advances_offset(tmp_path, http, api, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "1:x")
    api.fail_next_polls = 3
    api.push({"update_id": 41, **owner_text("/settings")})
    mod, _ = await _start(tmp_path, http)
    await api.wait_for("sendMessage")
    polls = await api.wait_for("getUpdates", count=5)
    assert polls[3]["offset"] == 0          # first successful poll
    assert polls[4]["offset"] == 42         # acknowledged update 41
    await mod.shutdown()


async def test_shutdown_cancels_blocked_poll(tmp_path, http, api, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "1:x")
    mod, _ = await _start(tmp_path, http)
    await api.wait_for("getUpdates")
    task = mod._task
    await asyncio.wait_for(mod.shutdown(), timeout=1)
    assert task is not None and task.cancelled()


async def test_handler_exception_does_not_kill_loop(tmp_path, http, api, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "1:x")

    def boom(_c):
        raise RuntimeError("boom")

    broken = Toggle("x", "y", "Broken", is_on=boom, set=lambda c, v: None, chats=lambda: {1})
    mod, _ = await _start(tmp_path, http, toggles=[broken])
    api.push(owner_text(ui.BTN_OVERVIEW))
    api.push(owner_text("/start"))
    sends = await api.wait_for("sendMessage")
    assert sends[0]["text"] == ui.PICKER_PROMPT
    await mod.shutdown()


# ---------- messages ----------

async def _active(tmp_path, http, api, monkeypatch, **kw):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "1:x")
    mod, tg = await _start(tmp_path, http, **kw)
    await api.wait_for("getUpdates")
    return mod, tg


@pytest.mark.parametrize("text", ["/start", "/settings", "/settings@my_bot", "/start abc"])
async def test_start_and_settings_send_picker(tmp_path, http, api, monkeypatch, text):
    mod, _ = await _active(tmp_path, http, api, monkeypatch)
    await mod.handle_update(owner_text(text))
    [send] = api.of("sendMessage")
    assert send["chat_id"] == BOT_CHAT
    assert send["text"] == ui.PICKER_PROMPT
    assert send["reply_markup"] == ui.picker_keyboard()
    await mod.shutdown()


async def test_ignores_non_owner_and_non_private(tmp_path, http, api, monkeypatch):
    store: dict[int, bool] = {}
    mod, _ = await _active(
        tmp_path, http, api, monkeypatch,
        toggles=[_dict_toggle("correcting", "auto_fix", "F", store)],
    )
    await mod.handle_update(owner_text("/settings", from_id=666))
    await mod.handle_update(owner_text("/settings", chat_type="group"))
    await mod.handle_update(callback("t:5:correcting.auto_fix", from_id=666))
    await mod.handle_update({"message": {"text": "/settings", "chat": {"id": 1, "type": "private"}}})
    assert api.of("sendMessage") == [] and api.of("answerCallbackQuery") == []
    assert store == {}
    await mod.shutdown()


async def test_users_shared_opens_menu_with_real_state(tmp_path, http, api, monkeypatch):
    fix: dict[int, bool] = {5: True}
    draft: dict[int, bool] = {}
    mod, _ = await _active(tmp_path, http, api, monkeypatch, toggles=[
        _dict_toggle("drafting", "auto_draft", "Antwortvorschläge", draft),
        _dict_toggle("correcting", "auto_fix", "Korrektur vor dem Senden", fix),
    ])
    await mod.handle_update(users_shared(5, "Mara", "M"))
    [send] = api.of("sendMessage")
    assert send["text"] == "⚙️ Einstellungen für: Mara M"
    assert [row[0]["text"] for row in send["reply_markup"]["inline_keyboard"]] == [
        "✅ Korrektur vor dem Senden", "❌ Antwortvorschläge",
    ]
    await mod.shutdown()


async def test_chat_shared_keeps_supergroup_id(tmp_path, http, api, monkeypatch):
    store: dict[int, bool] = {}
    mod, _ = await _active(
        tmp_path, http, api, monkeypatch,
        toggles=[_dict_toggle("correcting", "auto_fix", "F", store)],
    )
    await mod.handle_update(chat_shared(-1001234567890, "Team"))
    [send] = api.of("sendMessage")
    assert send["text"] == "⚙️ Einstellungen für: Team"
    assert send["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "t:-1001234567890:correcting.auto_fix"
    await mod.shutdown()


async def test_menu_name_falls_back_to_telethon_then_id(tmp_path, http, api, monkeypatch):
    tg = FakeTelegramClient(names={7: "Bob"})
    store: dict[int, bool] = {}
    mod, _ = await _active(
        tmp_path, http, api, monkeypatch, tg=tg,
        toggles=[_dict_toggle("correcting", "auto_fix", "F", store)],
    )
    await mod.handle_update(users_shared(7))
    await mod.handle_update(users_shared(8))
    assert [s["text"] for s in api.of("sendMessage")] == [
        "⚙️ Einstellungen für: Bob", "⚙️ Einstellungen für: 8",
    ]
    await mod.shutdown()


async def test_menu_without_features(tmp_path, http, api, monkeypatch):
    mod, _ = await _active(tmp_path, http, api, monkeypatch, toggles=[])
    await mod.handle_update(users_shared(5, "Mara"))
    [send] = api.of("sendMessage")
    assert ui.NO_FEATURES in send["text"] and "reply_markup" not in send
    await mod.shutdown()


# ---------- callbacks ----------

async def test_toggle_callback_flips_state_toasts_and_edits_in_place(tmp_path, http, api, monkeypatch):
    store: dict[int, bool] = {}
    mod, _ = await _active(
        tmp_path, http, api, monkeypatch,
        toggles=[_dict_toggle("correcting", "auto_fix", "Korrektur vor dem Senden", store)],
    )
    await mod.handle_update(callback("t:5:correcting.auto_fix", message_id=555))
    assert store == {5: True}
    [ans] = api.of("answerCallbackQuery")
    assert ans == {"callback_query_id": "cb-t:5:correcting.auto_fix", "text": "Korrektur vor dem Senden: an"}
    [edit] = api.of("editMessageReplyMarkup")
    assert edit["chat_id"] == BOT_CHAT and edit["message_id"] == 555
    assert edit["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ Korrektur vor dem Senden"

    await mod.handle_update(callback("t:5:correcting.auto_fix"))
    assert store == {5: False}
    assert api.of("answerCallbackQuery")[1]["text"] == "Korrektur vor dem Senden: aus"
    await mod.shutdown()


async def test_toggle_callback_state_write_error(tmp_path, http, api, monkeypatch):
    def failing_set(c, v):
        raise StateWriteError("disk full")

    t = Toggle("correcting", "auto_fix", "F", is_on=lambda c: False, set=failing_set, chats=set)
    mod, _ = await _active(tmp_path, http, api, monkeypatch, toggles=[t])
    await mod.handle_update(callback("t:5:correcting.auto_fix"))
    [ans] = api.of("answerCallbackQuery")
    assert ans["show_alert"] is True and "fehlgeschlagen" in ans["text"]
    assert api.of("editMessageReplyMarkup") == []
    await mod.shutdown()


async def test_unknown_toggle_and_garbage_callbacks_are_answered(tmp_path, http, api, monkeypatch):
    mod, _ = await _active(tmp_path, http, api, monkeypatch, toggles=[])
    await mod.handle_update(callback("t:5:media_reply.auto_media"))
    await mod.handle_update(callback("d:5"))
    assert len(api.of("answerCallbackQuery")) == 2
    assert api.of("editMessageReplyMarkup") == []
    await mod.shutdown()


async def test_open_callback_sends_menu(tmp_path, http, api, monkeypatch):
    store: dict[int, bool] = {-100777: True}
    mod, _ = await _active(
        tmp_path, http, api, monkeypatch,
        toggles=[_dict_toggle("correcting", "auto_fix", "F", store)],
    )
    await mod.handle_update(callback("o:-100777"))
    assert len(api.of("answerCallbackQuery")) == 1
    [send] = api.of("sendMessage")
    assert send["text"] == "⚙️ Einstellungen für: -100777"
    assert send["reply_markup"]["inline_keyboard"][0][0]["text"] == "✅ F"
    await mod.shutdown()


# ---------- overview ----------

async def test_overview_lists_only_chats_with_something_on(tmp_path, http, api, monkeypatch):
    fix = {5: True, 6: False}
    media = {-100777: True, 5: True}
    tg = FakeTelegramClient(names={5: "Mara", -100777: "Team"})
    mod, _ = await _active(tmp_path, http, api, monkeypatch, tg=tg, toggles=[
        _dict_toggle("media_reply", "auto_media", "Medien-Antworten", media),
        _dict_toggle("correcting", "auto_fix", "Korrektur vor dem Senden", fix),
    ])
    await mod.handle_update(owner_text(ui.BTN_OVERVIEW))
    [send] = api.of("sendMessage")
    assert send["text"] == (
        "📋 Aktiv:\n\n"
        "• Mara: Korrektur vor dem Senden, Medien-Antworten\n"
        "• Team: Medien-Antworten"
    )
    assert [r[0]["callback_data"] for r in send["reply_markup"]["inline_keyboard"]] == ["o:5", "o:-100777"]
    await mod.shutdown()


async def test_overview_empty(tmp_path, http, api, monkeypatch):
    mod, _ = await _active(tmp_path, http, api, monkeypatch, toggles=[])
    await mod.handle_update(owner_text(ui.BTN_OVERVIEW))
    [send] = api.of("sendMessage")
    assert send == {"chat_id": BOT_CHAT, "text": ui.NOTHING_ON}
    await mod.shutdown()


# ---------- other modules' inline buttons ----------

async def test_callbacks_offered_to_module_handlers_first(tmp_path, http, api, monkeypatch):
    store: dict[int, bool] = {}
    mod, _ = await _active(
        tmp_path, http, api, monkeypatch,
        toggles=[_dict_toggle("correcting", "auto_fix", "F", store)],
    )
    seen: list[str] = []

    async def meme_handler(q):
        seen.append(q["data"])
        return q["data"].startswith("meme:")

    mod._ctx.services.handlers.append(meme_handler)
    await mod.handle_update(callback("meme:abcd:s"))
    assert seen == ["meme:abcd:s"]
    assert api.of("answerCallbackQuery") == []      # handled by the module

    await mod.handle_update(callback("t:5:correcting.auto_fix"))
    assert store == {5: True}                       # not claimed → normal toggle
    await mod.shutdown()


async def test_non_owner_callbacks_never_reach_module_handlers(tmp_path, http, api, monkeypatch):
    mod, _ = await _active(tmp_path, http, api, monkeypatch)
    seen: list[str] = []

    async def handler(q):
        seen.append(q["data"])
        return True

    mod._ctx.services.handlers.append(handler)
    await mod.handle_update(callback("meme:abcd:s", from_id=666))
    assert seen == []
    await mod.shutdown()
