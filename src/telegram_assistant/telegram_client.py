"""Telegram client protocol. Concrete implementation is in telethon_client.py."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .events import Message


class TelegramClient(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        files: list[Path] | None = None,
    ) -> None: ...

    async def write_draft(self, chat_id: int, text: str) -> None: ...

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None: ...

    async def fetch_history(self, chat_id: int, n: int) -> list[Message]: ...

    async def download_media(self, message_id: int, chat_id: int, dest_dir: Path) -> Path: ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
