"""Tests for the minimal Bot API client."""
from __future__ import annotations

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.modules.settings_menu.bot_api import BotApi, BotApiError

URL = "https://api.telegram.org/bot123:abc/"


async def test_call_posts_json_and_unwraps_result():
    with aioresponses() as m:
        m.post(URL + "sendMessage", payload={"ok": True, "result": {"message_id": 7}})
        async with aiohttp.ClientSession() as http:
            result = await BotApi(http, "123:abc").call("sendMessage", chat_id=1, text="hi")
        assert result == {"message_id": 7}
        sent = [c for k, calls in m.requests.items() for c in calls if k[0] == "POST"]
        assert sent[0].kwargs["json"] == {"chat_id": 1, "text": "hi"}


async def test_call_raises_bot_api_error_without_leaking_token():
    with aioresponses() as m:
        m.post(
            URL + "getUpdates", status=409,
            payload={"ok": False, "error_code": 409, "description": "Conflict: terminated by other getUpdates request"},
        )
        async with aiohttp.ClientSession() as http:
            with pytest.raises(BotApiError) as ei:
                await BotApi(http, "123:abc").call("getUpdates", timeout=0)
    assert ei.value.error_code == 409
    assert "Conflict" in ei.value.description
    assert "123:abc" not in str(ei.value)


async def test_call_non_json_response():
    with aioresponses() as m:
        m.post(URL + "getMe", status=502, body="<html>bad gateway</html>")
        async with aiohttp.ClientSession() as http:
            with pytest.raises(BotApiError) as ei:
                await BotApi(http, "123:abc").call("getMe")
    assert ei.value.error_code == 502
    assert "123:abc" not in str(ei.value)
