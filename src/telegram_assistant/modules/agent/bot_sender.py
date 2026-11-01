"""Thin wrappers over Telegram's Bot API ``sendMessage`` / ``sendPhoto``.

A standalone async function — no class, no state — so it's trivial to
swap out in tests via dependency injection.

Returns the ``message_id`` of the sent message so callers can track
replies (used by the agent module's clarification feature) or edit the
message later (meme previews).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

async def send_photo(
    http: aiohttp.ClientSession,
    token: str,
    *,
    chat_id: int,
    photo: Path,
    caption: str | None = None,
    reply_markup: dict[str, Any] | None = None,
) -> int:
    """Upload a local image file (multipart ``sendPhoto``) to the bot chat.

    Returns the ``message_id`` assigned by Telegram. Raises
    :class:`RuntimeError` on failure — deliberately not
    ``raise_for_status()``, whose message embeds the URL (i.e. the token).
    """
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    form = aiohttp.FormData()
    form.add_field("chat_id", str(chat_id))
    if caption:
        form.add_field("caption", caption)
    if reply_markup is not None:
        form.add_field("reply_markup", json.dumps(reply_markup, ensure_ascii=False))
    form.add_field(
        "photo", photo.read_bytes(), filename=photo.name, content_type="image/png",
    )
    async with http.post(url, data=form) as resp:
        try:
            data = await resp.json(content_type=None)
        except (aiohttp.ContentTypeError, ValueError):
            data = None
    if not isinstance(data, dict) or not data.get("ok"):
        desc = data.get("description", "unknown error") if isinstance(data, dict) else "non-JSON response"
        raise RuntimeError(f"sendPhoto failed (HTTP {resp.status}): {desc}")
    return int(data["result"]["message_id"])
