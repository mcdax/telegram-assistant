"""In-memory TelegramClient for tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from telegram_assistant.events import Message


@dataclass
class SentMessage:
    chat_id: int
    text: str | None
    reply_to: int | None
    files: list[Path]


@dataclass
class EditedMessage:
    chat_id: int
    message_id: int
    text: str


@dataclass
class FakeTelegramClient:
    drafts: dict[int, str] = field(default_factory=dict)
    sent: list[SentMessage] = field(default_factory=list)
    edits: list[EditedMessage] = field(default_factory=list)
    history: dict[int, list[Message]] = field(default_factory=dict)

    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        files: list[Path] | None = None,
    ) -> None:
        self.sent.append(SentMessage(chat_id, text, reply_to, list(files or [])))

    async def write_draft(self, chat_id: int, text: str) -> None:
        self.drafts[chat_id] = text

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.edits.append(EditedMessage(chat_id, message_id, text))

    async def fetch_history(self, chat_id: int, n: int) -> list[Message]:
        return list(self.history.get(chat_id, []))[-n:]

    async def download_media(self, message_id: int, chat_id: int, dest_dir: Path) -> Path:
        p = dest_dir / f"msg-{message_id}.bin"
        p.write_bytes(b"fake-media")
        return p

    async def connect(self) -> None:
        return

    async def disconnect(self) -> None:
        return

    def seed_history(self, chat_id: int, messages: list[Message]) -> None:
        self.history[chat_id] = list(messages)


def make_message(
    chat_id: int,
    sender: str,
    text: str,
    message_id: int = 1,
    outgoing: bool = False,
) -> Message:
    return Message(
        chat_id=chat_id,
        message_id=message_id,
        sender=sender,
        timestamp=datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc),
        text=text,
        outgoing=outgoing,
    )
