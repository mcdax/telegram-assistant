"""Tests for the Telegram Bot API sendMessage wrapper."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.modules.agent.bot_sender import send_photo, send_text


async def test_send_text_posts_to_bot_api():
    with aioresponses() as m:
        m.post(
            "https://api.telegram.org/bot123:abc/sendMessage",
            payload={"ok": True, "result": {"message_id": 42}},
        )
        async with aiohttp.ClientSession() as http:
            await send_text(http, "123:abc", chat_id=42, text="hi")

        sent = [c for k, calls in m.requests.items() for c in calls if k[0] == "POST"]
        assert len(sent) == 1
        assert sent[0].kwargs["json"] == {"chat_id": 42, "text": "hi"}


async def test_send_text_raises_on_http_error():
    with aioresponses() as m:
        m.post(
            "https://api.telegram.org/bot123:abc/sendMessage",
            status=400,
            payload={"ok": False, "description": "Bad Request"},
        )
        async with aiohttp.ClientSession() as http:
            with pytest.raises(aiohttp.ClientResponseError):
                await send_text(http, "123:abc", chat_id=42, text="hi")


async def test_send_photo_uploads_multipart(tmp_path):
    img = tmp_path / "m.png"
    img.write_bytes(b"\x89PNG")
    with aioresponses() as m:
        m.post(
            "https://api.telegram.org/bot123:abc/sendPhoto",
            payload={"ok": True, "result": {"message_id": 7}},
        )
        async with aiohttp.ClientSession() as http:
            msg_id = await send_photo(
                http, "123:abc", chat_id=42, photo=img, caption="cap",
                reply_markup={"inline_keyboard": [[{"text": "x", "callback_data": "y"}]]},
            )
        [call] = [c for k, calls in m.requests.items() for c in calls if k[0] == "POST"]
    assert msg_id == 7
    form = call.kwargs["data"]
    assert isinstance(form, aiohttp.FormData)
    names = [f[0]["name"] for f in form._fields]
    assert names == ["chat_id", "caption", "reply_markup", "photo"]


async def test_send_photo_error_does_not_leak_token(tmp_path):
    img = tmp_path / "m.png"
    img.write_bytes(b"\x89PNG")
    with aioresponses() as m:
        m.post(
            "https://api.telegram.org/bot123:abc/sendPhoto",
            status=400,
            payload={"ok": False, "description": "Bad Request: wrong file"},
        )
        async with aiohttp.ClientSession() as http:
            with pytest.raises(RuntimeError, match="wrong file") as err:
                await send_photo(http, "123:abc", chat_id=42, photo=img)
    assert "123:abc" not in str(err.value)
