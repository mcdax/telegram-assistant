"""Minimal async Telegram Bot API client over the shared aiohttp session.

Only what the settings menu needs: a generic JSON ``call`` that unwraps
``{"ok": true, "result": …}`` and raises :class:`BotApiError` otherwise.
"""
from __future__ import annotations

from typing import Any, Protocol

import aiohttp

API_BASE = "https://api.telegram.org"


class BotApiError(RuntimeError):
    def __init__(self, method: str, description: str, error_code: int | None = None) -> None:
        super().__init__(f"{method}: {description} (code={error_code})")
        self.method = method
        self.description = description
        self.error_code = error_code


class BotApiLike(Protocol):
    async def call(self, method: str, *, timeout_s: float | None = None, **params: Any) -> Any: ...


class BotApi:
    def __init__(self, http: aiohttp.ClientSession, token: str, *, base: str = API_BASE) -> None:
        self._http = http
        self._url = f"{base}/bot{token}/"

    async def call(self, method: str, *, timeout_s: float | None = None, **params: Any) -> Any:
        timeout = aiohttp.ClientTimeout(total=timeout_s) if timeout_s is not None else None
        async with self._http.post(self._url + method, json=params, timeout=timeout) as resp:
            try:
                data = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError):
                # Not raise_for_status(): its message embeds the URL, i.e. the token.
                raise BotApiError(method, f"non-JSON response (HTTP {resp.status})", resp.status)
        if not isinstance(data, dict) or not data.get("ok"):
            desc = data.get("description", "unknown error") if isinstance(data, dict) else "bad response"
            code = data.get("error_code", resp.status) if isinstance(data, dict) else resp.status
            raise BotApiError(method, str(desc), code)
        return data.get("result")
