"""Tests for the meme module: trigger, retry path, preview and buttons."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import MarkerMatch
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.meme.module import (
    BTN_AGAIN,
    BTN_OTHER,
    BTN_SEND,
    HINT_EMPTY,
    MemeModule,
    parse_callback,
)
from telegram_assistant.state import RuntimeState
from tests.fakes.bot_api import BOT_CHAT, OWNER, FakeBotApi, callback
from tests.fakes.llm import ScriptedLLM
from tests.fakes.telegram import FakeTelegramClient, make_message

CHAT = 42
PNG = b"\x89PNG\r\n\x1a\nfake"
MEMEGEN = re.compile(r"^https://api\.memegen\.link/images/.*")
DRAKE = '{"kind":"template","template_id":"drake","lines":["Arbeiten","Memes machen"]}'
FRY = '{"kind":"template","template_id":"fry","lines":["Not sure if","meme"]}'


class FakeServices:
    def __init__(self, configs: dict[str, dict[str, Any]]) -> None:
        self._configs = configs

    def toggles(self) -> list:
        return []

    def module_config(self, name: str) -> dict[str, Any]:
        return dict(self._configs.get(name, {}))

    def bot_callback_handlers(self) -> list:
        return []


class Bot:
    """Records bot_sender.send_text / send_photo calls."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.photos: list[dict[str, Any]] = []
        self._next_id = 500

    async def send_text(self, http, token, *, chat_id: int, text: str) -> int:
        self.texts.append(text)
        self._next_id += 1
        return self._next_id

    async def send_photo(self, http, token, *, chat_id, photo: Path, caption=None, reply_markup=None) -> int:
        assert photo.read_bytes() == PNG
        self._next_id += 1
        self.photos.append({
            "chat_id": chat_id, "photo": photo, "caption": caption,
            "reply_markup": reply_markup, "message_id": self._next_id,
        })
        return self._next_id


async def wait_until(cond, timeout: float = 2.0) -> None:
    async def _loop() -> None:
        while not cond():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(_loop(), timeout)


@pytest.fixture
async def http():
    session = aiohttp.ClientSession()
    yield session
    await session.close()


@pytest.fixture
def api(monkeypatch) -> FakeBotApi:
    fake = FakeBotApi()
    monkeypatch.setattr(MemeModule, "_api_override", lambda _http, _tok: fake)
    return fake


@pytest.fixture
def memegen():
    with aioresponses() as m:
        yield m


async def _start(
    tmp_path, http, llm, *, config: dict | None = None, tg: FakeTelegramClient | None = None,
) -> tuple[MemeModule, FakeTelegramClient, Bot]:
    tg = tg or FakeTelegramClient()
    tg.seed_history(CHAT, [make_message(CHAT, "alice", "Montag schon wieder")])
    tg.names[CHAT] = "Alice"
    bot = Bot()
    ctx = ModuleContext(
        tg=tg, llm=llm, http=http,
        config={"enabled": True, "debounce_s": 0.01, "cooldown_s": 0, **(config or {})},
        state=RuntimeState(tmp_path / "state.toml").for_module("meme"),
        log=logging.getLogger("meme"),
        services=FakeServices({
            "agent": {"bot_token_env": "AGENT_BOT_TOKEN", "target_chat_id": OWNER},
            "settings_menu": {"enabled": True},
        }),
    )
    mod = MemeModule(send_text=bot.send_text, send_photo=bot.send_photo)
    await mod.init(ctx)
    return mod, tg, bot


async def _type(mod: MemeModule, text: str, chat_id: int = CHAT) -> None:
    [marker] = mod.markers()
    ok, remainder = marker.match(text)
    assert ok
    await mod.on_draft_update(DraftUpdate(chat_id=chat_id, text=text), MarkerMatch("meme", marker, remainder))


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "123:abc")


# ---------- trigger ----------

async def test_marker_defaults(tmp_path, http, api):
    mod, _, _ = await _start(tmp_path, http, ScriptedLLM(DRAKE))
    [m] = mod.markers()
    assert (m.name, m.trigger, m.priority) == ("meme", "/meme", 55)
    await mod.shutdown()


async def test_empty_description_posts_hint(tmp_path, http, api):
    llm = ScriptedLLM(DRAKE)
    mod, tg, bot = await _start(tmp_path, http, llm)
    tg.drafts[CHAT] = "/meme"
    await _type(mod, "/meme  ")
    await wait_until(lambda: bot.texts)
    assert bot.texts == [HINT_EMPTY]
    assert tg.drafts[CHAT] == ""
    assert llm.calls == []
    await mod.shutdown()


async def test_plain_draft_cancels_debounce(tmp_path, http, api):
    llm = ScriptedLLM(DRAKE)
    mod, tg, _ = await _start(tmp_path, http, llm, config={"debounce_s": 0.05})
    await _type(mod, "/meme montag")
    await mod.on_plain_draft_update(DraftUpdate(chat_id=CHAT, text="montag"))
    await asyncio.sleep(0.1)
    assert llm.calls == [] and CHAT not in tg.drafts
    await mod.shutdown()


async def test_happy_path_posts_preview(tmp_path, http, api, memegen):
    memegen.get(MEMEGEN, body=PNG, content_type="image/png")
    llm = ScriptedLLM("```json\n" + DRAKE + "\n```")
    mod, tg, bot = await _start(tmp_path, http, llm)
    tg.drafts[CHAT] = "/meme montag"
    await _type(mod, "/meme montags-blues")
    await wait_until(lambda: bot.photos)

    assert tg.drafts[CHAT] == ""
    [prompt] = llm.calls
    assert "Description (from the user): montags-blues" in prompt
    assert "UNTRUSTED DATA" in prompt and "<untrusted_chat_history>" in prompt
    assert "Montag schon wieder" in prompt
    [(method, url)] = [k for k in memegen.requests]
    assert method == "GET"
    assert str(url) == "https://api.memegen.link/images/drake/Arbeiten/Memes_machen.png"

    [photo] = bot.photos
    assert photo["chat_id"] == OWNER
    assert photo["caption"] == "🖼 Meme für: Alice\nTemplate: drake\n„montags-blues“"
    rows = photo["reply_markup"]["inline_keyboard"]
    assert [[b["text"] for b in row] for row in rows] == [[BTN_SEND], [BTN_AGAIN, BTN_OTHER]]
    assert all(len(b["callback_data"].encode()) <= 64 for row in rows for b in row)
    assert bot.texts == []
    await mod.shutdown()
    assert not photo["photo"].exists()  # temp dir cleaned on shutdown


async def test_invalid_json_retries_once_with_error(tmp_path, http, api, memegen):
    memegen.get(MEMEGEN, body=PNG, content_type="image/png")
    llm = ScriptedLLM("sorry, no idea", DRAKE)
    mod, _, bot = await _start(tmp_path, http, llm)
    await _type(mod, "/meme montag")
    await wait_until(lambda: bot.photos)
    assert len(llm.calls) == 2
    assert llm.calls[1].startswith(llm.calls[0])
    assert "<previous_answer>\nsorry, no idea\n</previous_answer>" in llm.calls[1]
    assert "It failed: Antwort enthält kein JSON-Objekt" in llm.calls[1]
    await mod.shutdown()


async def test_unknown_template_4xx_retries(tmp_path, http, api, memegen):
    memegen.get(MEMEGEN, status=404, body="template not found")
    memegen.get(MEMEGEN, body=PNG, content_type="image/png")
    llm = ScriptedLLM('{"kind":"template","template_id":"nope","lines":["a"]}', DRAKE)
    mod, _, bot = await _start(tmp_path, http, llm)
    await _type(mod, "/meme montag")
    await wait_until(lambda: bot.photos)
    assert "memegen HTTP 404" in llm.calls[1]
    assert bot.photos[0]["caption"].endswith("Template: drake\n„montag“")
    await mod.shutdown()


async def test_second_failure_reports_error(tmp_path, http, api, memegen):
    llm = ScriptedLLM("nope", "still nope")
    mod, _, bot = await _start(tmp_path, http, llm)
    await _type(mod, "/meme montag")
    await wait_until(lambda: bot.texts)
    assert len(llm.calls) == 2
    assert bot.texts == ["❌ meme: Antwort enthält kein JSON-Objekt"]
    assert bot.photos == []
    await mod.shutdown()


async def test_llm_exception_reports_error(tmp_path, http, api):
    llm = ScriptedLLM(RuntimeError("gateway down"))
    mod, _, bot = await _start(tmp_path, http, llm)
    await _type(mod, "/meme montag")
    await wait_until(lambda: bot.texts)
    assert bot.texts == ["❌ meme: gateway down"]
    await mod.shutdown()


async def test_cooldown_and_running_guard(tmp_path, http, api, memegen):
    memegen.get(MEMEGEN, body=PNG, content_type="image/png", repeat=True)
    gate = asyncio.Event()

    class SlowLLM(ScriptedLLM):
        async def run(self, agent, user_text):
            await gate.wait()
            return await super().run(agent, user_text)

    llm = SlowLLM(DRAKE)
    mod, _, bot = await _start(tmp_path, http, llm, config={"cooldown_s": 60})
    await _type(mod, "/meme eins")
    await wait_until(lambda: mod._is_running(CHAT))
    await _type(mod, "/meme zwei")
    await wait_until(lambda: bot.texts)
    assert bot.texts == ["⏳ meme: läuft bereits für diesen Chat"]
    gate.set()
    await wait_until(lambda: bot.photos)
    await wait_until(lambda: not mod._is_running(CHAT))
    await _type(mod, "/meme drei")
    await wait_until(lambda: len(bot.texts) == 2)
    assert bot.texts[1].startswith("⏳ meme: Cooldown")
    assert len(bot.photos) == 1
    await mod.shutdown()


async def test_shutdown_cancels_running_generation_cleanly(tmp_path, http, api):
    started = asyncio.Event()

    class HangingLLM(ScriptedLLM):
        async def run(self, agent, user_text):
            started.set()
            await asyncio.sleep(10)
            return ""

    mod, _, bot = await _start(tmp_path, http, HangingLLM())
    await _type(mod, "/meme montag")
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.wait_for(mod.shutdown(), 1)
    assert bot.texts == [] and bot.photos == []


# ---------- buttons ----------

async def _preview(tmp_path, http, memegen, *responses: str):
    memegen.get(MEMEGEN, body=PNG, content_type="image/png", repeat=True)
    llm = ScriptedLLM(*responses)
    mod, tg, bot = await _start(tmp_path, http, llm)
    await _type(mod, "/meme montag")
    await wait_until(lambda: bot.photos)
    return mod, tg, bot, llm


def _data(bot: Bot, idx: int, row: int, col: int = 0) -> str:
    return bot.photos[idx]["reply_markup"]["inline_keyboard"][row][col]["callback_data"]


async def test_send_button_sends_into_source_chat(tmp_path, http, api, memegen):
    mod, tg, bot, _ = await _preview(tmp_path, http, memegen, DRAKE)
    path = bot.photos[0]["photo"]
    assert await mod.on_bot_callback(callback(_data(bot, 0, 0), message_id=bot.photos[0]["message_id"])["callback_query"])
    await wait_until(lambda: api.of("editMessageCaption"))

    [sent] = tg.sent
    assert sent.chat_id == CHAT and sent.files == [path] and sent.text is None
    [ans] = api.of("answerCallbackQuery")
    assert ans["text"] == "📤 Wird gesendet…"
    [edit] = api.of("editMessageCaption")
    assert edit["chat_id"] == BOT_CHAT and edit["message_id"] == bot.photos[0]["message_id"]
    assert edit["caption"].endswith("✅ gesendet")
    assert edit["reply_markup"] == {"inline_keyboard": []}
    assert not path.exists()

    # A second tap on the same (now consumed) preview is "expired".
    await mod.on_bot_callback(callback(_data(bot, 0, 0))["callback_query"])
    assert api.of("answerCallbackQuery")[-1]["text"] == "⌛ Abgelaufen"
    assert len(tg.sent) == 1
    await mod.shutdown()


async def test_again_button_regenerates_new_variant(tmp_path, http, api, memegen):
    mod, tg, bot, llm = await _preview(tmp_path, http, memegen, DRAKE, DRAKE)
    old_path = bot.photos[0]["photo"]
    await mod.on_bot_callback(callback(_data(bot, 0, 1, 0), message_id=777)["callback_query"])
    await wait_until(lambda: len(bot.photos) == 2)

    assert "Produce a DIFFERENT variant" in llm.calls[1]
    assert '"template_id": "drake"' in llm.calls[1]
    assert "Description (from the user): montag" in llm.calls[1]
    assert api.of("answerCallbackQuery")[0]["text"] == "🔁 Generiere neu…"
    [rm] = api.of("editMessageReplyMarkup")
    assert rm["message_id"] == 777 and rm["reply_markup"] == {"inline_keyboard": []}
    assert not old_path.exists()
    assert _data(bot, 1, 0) != _data(bot, 0, 0)
    assert tg.sent == []
    await mod.shutdown()


async def test_other_template_button_excludes_previous(tmp_path, http, api, memegen):
    mod, _, bot, llm = await _preview(tmp_path, http, memegen, DRAKE, FRY)
    await mod.on_bot_callback(callback(_data(bot, 0, 1, 1))["callback_query"])
    await wait_until(lambda: len(bot.photos) == 2)
    assert 'Use a DIFFERENT template than before — do not use the template "drake" again' in llm.calls[1]
    assert "Template: fry" in bot.photos[1]["caption"]
    await mod.shutdown()


async def test_foreign_callback_not_handled(tmp_path, http, api):
    mod, _, _ = await _start(tmp_path, http, ScriptedLLM(DRAKE))
    assert await mod.on_bot_callback(callback("t:5:drafting.auto_draft")["callback_query"]) is False
    assert api.calls == []
    await mod.shutdown()


async def test_pending_expires(tmp_path, http, api, memegen):
    mod, tg, bot, _ = await _preview(tmp_path, http, memegen, DRAKE)
    path = bot.photos[0]["photo"]
    mod._ttl_s = 0
    await asyncio.sleep(0.01)
    await mod.on_bot_callback(callback(_data(bot, 0, 0))["callback_query"])
    assert api.of("answerCallbackQuery")[0]["text"] == "⌛ Abgelaufen"
    assert api.of("editMessageReplyMarkup")
    assert tg.sent == [] and not path.exists()
    await mod.shutdown()


def test_parse_callback():
    assert parse_callback("meme:ab12cd34:s") == ("ab12cd34", "s")
    assert parse_callback("meme:ab12cd34:x") is None
    assert parse_callback("meme:ab12cd34") is None
    assert parse_callback("t:1:x") is None


async def test_send_disabled_without_token(tmp_path, http, api, memegen, monkeypatch):
    monkeypatch.delenv("AGENT_BOT_TOKEN")
    memegen.get(MEMEGEN, body=PNG, content_type="image/png")
    llm = ScriptedLLM(DRAKE)
    mod, _, bot = await _start(tmp_path, http, llm)
    assert mod.send_disabled
    await _type(mod, "/meme montag")
    await wait_until(lambda: llm.calls and not mod._is_running(CHAT))
    assert bot.photos == [] and bot.texts == []
    await mod.shutdown()
