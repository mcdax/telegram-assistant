"""Loop protection. Tracks last draft text this app wrote per chat."""
from __future__ import annotations


class LoopProtection:
    def __init__(self) -> None:
        self._last: dict[int, str] = {}

    def record(self, chat_id: int, text: str) -> None:
        self._last[chat_id] = text

    def is_our_write(self, chat_id: int, text: str) -> bool:
        return self._last.get(chat_id) == text
