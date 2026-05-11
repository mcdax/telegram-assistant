"""Tests for the Telegram Bot API sendMessage wrapper."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.modules.agent.bot_sender import send_text


async def test_send_text_posts_to_bot_api():
    with aioresponses() as m:
        m.post(
            "https://api.telegram.org/bot123:abc/sendMessage",
            payload={"ok": True, "result": {}},
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
