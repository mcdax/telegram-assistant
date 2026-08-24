"""Thin wrapper over Telegram's Bot API ``sendMessage`` endpoint.

A standalone async function — no class, no state — so it's trivial to
swap out in tests via dependency injection.

Returns the ``message_id`` of the sent message so callers can track
replies (used by the agent module's clarification feature).
"""
from __future__ import annotations

import aiohttp


async def send_text(
    http: aiohttp.ClientSession,
    token: str,
    *,
    chat_id: int,
    text: str,
) -> int:
    """POST a plain-text message to the configured Telegram bot chat.

    Returns the ``message_id`` assigned by Telegram.

    Raises :class:`aiohttp.ClientResponseError` on non-2xx responses.
    """
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with http.post(url, json={"chat_id": chat_id, "text": text}) as resp:
        resp.raise_for_status()
        data = await resp.json()
    return int(data["result"]["message_id"])