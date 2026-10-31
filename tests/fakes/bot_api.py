"""In-memory Telegram Bot API for settings-menu tests (no network)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeBotApi:
    """Records every call; ``getUpdates`` serves batches pushed via ``push``.

    ``fail_next_polls`` makes that many getUpdates calls raise, to exercise
    the backoff path.
    """

    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail_next_polls: int = 0
    _updates: asyncio.Queue = field(default_factory=asyncio.Queue)
    _next_message_id: int = 1000
    _next_update_id: int = 1

    async def call(self, method: str, *, timeout_s: float | None = None, **params: Any) -> Any:
        self.calls.append((method, params))
        if method == "getUpdates":
            if self.fail_next_polls > 0:
                self.fail_next_polls -= 1
                raise ConnectionError("network down")
            batch = await self._updates.get()
            return batch
        if method == "sendMessage":
            self._next_message_id += 1
            return {"message_id": self._next_message_id, "chat": {"id": params["chat_id"]}}
        return True

    def push(self, *updates: dict[str, Any]) -> None:
        batch = []
        for u in updates:
            u = dict(u)
            u.setdefault("update_id", self._next_update_id)
            self._next_update_id = u["update_id"] + 1
            batch.append(u)
        self._updates.put_nowait(batch)

    def of(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    async def wait_for(self, method: str, count: int = 1, timeout: float = 1.0) -> list[dict[str, Any]]:
        async def _wait() -> None:
            while len(self.of(method)) < count:
                await asyncio.sleep(0.005)
        await asyncio.wait_for(_wait(), timeout)
        return self.of(method)


OWNER = 15553658
BOT_CHAT = OWNER  # private chat with the bot: chat.id == user id


def owner_text(text: str, *, from_id: int = OWNER, chat_type: str = "private") -> dict[str, Any]:
    return {"message": {
        "message_id": 1, "from": {"id": from_id},
        "chat": {"id": from_id, "type": chat_type}, "text": text,
    }}


def users_shared(user_id: int, first: str | None = None, last: str | None = None) -> dict[str, Any]:
    user: dict[str, Any] = {"user_id": user_id}
    if first:
        user["first_name"] = first
    if last:
        user["last_name"] = last
    return {"message": {
        "message_id": 2, "from": {"id": OWNER}, "chat": {"id": BOT_CHAT, "type": "private"},
        "users_shared": {"request_id": 1, "users": [user]},
    }}


def chat_shared(chat_id: int, title: str | None = None) -> dict[str, Any]:
    cs: dict[str, Any] = {"request_id": 2, "chat_id": chat_id}
    if title:
        cs["title"] = title
    return {"message": {
        "message_id": 3, "from": {"id": OWNER}, "chat": {"id": BOT_CHAT, "type": "private"},
        "chat_shared": cs,
    }}


def callback(data: str, *, from_id: int = OWNER, message_id: int = 555) -> dict[str, Any]:
    return {"callback_query": {
        "id": f"cb-{data}", "from": {"id": from_id}, "data": data,
        "message": {"message_id": message_id, "chat": {"id": BOT_CHAT}},
    }}
