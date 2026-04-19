from __future__ import annotations

import aiohttp
from aioresponses import aioresponses

from telegram_assistant.events import Message
from telegram_assistant.modules.drafting.enricher import Enricher
from tests.fakes.telegram import make_message


MSG: list[Message] = [make_message(chat_id=1, sender="alice", text="hi", message_id=1)]


async def test_enricher_happy_path():
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post(
                "https://host/ctx",
                status=200,
                payload={"context": "it is 5pm"},
            )
            enricher = Enricher(
                http=http,
                url="https://host/ctx",
                auth_header=None,
                timeout_s=5,
            )
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == "it is 5pm"


async def test_enricher_non_2xx_returns_empty(caplog):
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post("https://host/ctx", status=500)
            enricher = Enricher(http=http, url="https://host/ctx", auth_header=None, timeout_s=5)
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == ""


async def test_enricher_timeout_returns_empty():
    import asyncio
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            async def slow(*a, **kw):
                await asyncio.sleep(5)
                raise asyncio.TimeoutError()

            mock.post("https://host/ctx", callback=slow)
            enricher = Enricher(http=http, url="https://host/ctx", auth_header=None, timeout_s=0)
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == ""


async def test_enricher_network_error_returns_empty():
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post("https://host/ctx", exception=aiohttp.ClientError("network error"))
            enricher = Enricher(http=http, url="https://host/ctx", auth_header=None, timeout_s=5)
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == ""


async def test_enricher_empty_url_returns_empty():
    async with aiohttp.ClientSession() as http:
        enricher = Enricher(http=http, url="", auth_header=None, timeout_s=5)
        out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
        assert out == ""


async def test_enricher_auth_header_passed():
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post("https://host/ctx", status=200, payload={"context": "ok"})
            enricher = Enricher(
                http=http, url="https://host/ctx", auth_header="Bearer X", timeout_s=5
            )
            await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            req = list(mock.requests.values())[0][0]
            assert req.kwargs["headers"]["Authorization"] == "Bearer X"
