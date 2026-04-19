"""External context enrichment. Best-effort HTTP call.

Failures (timeout, non-2xx, network error, empty URL) all yield "".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import aiohttp

from telegram_assistant.events import Message

log = logging.getLogger(__name__)


class Enricher:
    def __init__(
        self,
        http: aiohttp.ClientSession,
        url: str,
        auth_header: str | None,
        timeout_s: int,
    ) -> None:
        self._http = http
        self._url = url
        self._auth_header = auth_header
        self._timeout = timeout_s

    async def fetch(self, chat_id: int, chat_title: str, messages: Iterable[Message]) -> str:
        if not self._url:
            return ""

        payload = {
            "chat_id": chat_id,
            "chat_title": chat_title,
            "messages": [
                {
                    "sender": m.sender,
                    "timestamp": m.timestamp.isoformat(),
                    "text": m.text,
                }
                for m in messages
            ],
        }
        headers = {"Authorization": self._auth_header} if self._auth_header else {}

        try:
            async with self._http.post(
                self._url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status >= 300:
                    log.warning("enrichment returned %s", resp.status)
                    return ""
                data = await resp.json()
                return str(data.get("context", ""))
        except asyncio.TimeoutError:
            log.warning("enrichment timed out after %ss", self._timeout)
            return ""
        except aiohttp.ClientError as e:
            log.warning("enrichment network error: %s", e)
            return ""
