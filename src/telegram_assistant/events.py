"""Event types emitted onto the event bus."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Message:
    chat_id: int
    message_id: int
    sender: str
    timestamp: datetime
    text: str
    outgoing: bool


@dataclass(frozen=True)
class IncomingMessage:
    message: Message


@dataclass(frozen=True)
class OutgoingMessage:
    """Emitted when the user sends a message through their own account.

    Distinct from IncomingMessage so modules can handle the two flows
    independently (e.g. post-send correction vs. auto-drafting replies).
    """

    message: Message


@dataclass(frozen=True)
class DraftUpdate:
    chat_id: int
    text: str
