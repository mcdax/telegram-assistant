"""End-to-end: /meme draft → preview in the bot chat → "✅ Senden" button,
routed through settings_menu's single getUpdates loop."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import aiohttp
from aioresponses import aioresponses

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate
from telegram_assistant.modules.meme.module import MemeModule
from telegram_assistant.modules.settings_menu.module import SettingsMenuModule
from tests.fakes.bot_api import OWNER, FakeBotApi, callback
from tests.fakes.llm import RecordingLLM
from tests.fakes.telegram import FakeTelegramClient, make_message

PNG = b"\x89PNG\r\n\x1a\nfake"
SPEC = '{"kind":"template","template_id":"drake","lines":["Montag","Freitag"]}'


async def _until(cond, timeout: float = 2.0) -> None:
    async def _loop() -> None:
        while not cond():
            await asyncio.sleep(0.005)
    await asyncio.wait_for(_loop(), timeout)


async def test_meme_end_to_end(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "123:abc")
    api = FakeBotApi()
    monkeypatch.setattr(SettingsMenuModule, "_api_override", lambda _h, _t: api)
    monkeypatch.setattr(MemeModule, "_api_override", lambda _h, _t: api)

    photos: list[dict] = []

    async def fake_send_photo(http_, token, *, chat_id, photo, caption=None, reply_markup=None):
        photos.append({"chat_id": chat_id, "photo": photo, "caption": caption, "markup": reply_markup})
        return 900

    async def fake_send_text(http_, token, *, chat_id, text):
        raise AssertionError(f"unexpected bot text: {text}")

    import telegram_assistant.modules.meme.module as meme_module
    monkeypatch.setattr(meme_module._bot_sender, "send_photo", fake_send_photo)
    monkeypatch.setattr(meme_module._bot_sender, "send_text", fake_send_text)

    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "endlich Wochenende")])
    tg.names[42] = "Alice"
    tg.drafts[42] = "/meme wochenende"
    llm = RecordingLLM(SPEC)
    modules_cfg = {
        "agent": {"enabled": False, "bot_token_env": "AGENT_BOT_TOKEN", "target_chat_id": OWNER},
        "settings_menu": {"enabled": True},
        "meme": {"enabled": True, "debounce_s": 0.05, "markers": {}},
    }
    with aioresponses(passthrough_unmatched=False) as m:
        m.get("https://api.memegen.link/templates/", payload=[
            {"id": "drake", "name": "Drake", "lines": 2, "example": {"text": ["no", "yes"]}}
        ])
        m.get(re.compile(r"^https://api\.memegen\.link/images/drake/Montag/Freitag\.png$"),
              body=PNG, content_type="image/png")
        http = aiohttp.ClientSession()
        app = App(tg=tg, llm=llm, http=http, state_path=tmp_path / "state.toml")
        await app.start(modules_cfg)
        await app.inject_draft_update(DraftUpdate(chat_id=42, text="/meme wochenende"))
        await app.drain()
        await _until(lambda: photos)

        assert tg.drafts[42] == ""
        assert "Description (from the user): wochenende" in llm.calls[0]
        [photo] = photos
        assert photo["chat_id"] == OWNER
        assert photo["caption"].startswith("🖼 Meme für: Alice\nTemplate: drake")
        send_data = photo["markup"]["inline_keyboard"][0][0]["callback_data"]

        # The tap arrives through settings_menu's poll loop.
        api.push(callback(send_data, message_id=900))
        await api.wait_for("editMessageCaption")
        [sent] = tg.sent
        assert sent.chat_id == 42 and sent.files == [photo["photo"]]
        assert api.of("answerCallbackQuery")[0]["text"] == "📤 Wird gesendet…"

        await app.stop()
        await http.close()
