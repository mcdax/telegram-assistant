"""Concrete Telethon-backed TelegramClient.

This is a thin adapter: translate Telethon events to our Message / IncomingMessage /
DraftUpdate types, and map our API methods onto Telethon calls.
"""
from __future__ import annotations

import logging
from datetime import timezone
from pathlib import Path
from typing import Awaitable, Callable

from telethon import TelegramClient as _Telethon
from telethon import events as _events
from telethon.tl.functions.messages import SaveDraftRequest
from telethon.utils import get_peer_id

from .events import DraftUpdate, IncomingMessage, Message, MessageEdited, OutgoingMessage

log = logging.getLogger(__name__)

OnIncoming = Callable[[IncomingMessage], Awaitable[None]]
OnOutgoing = Callable[[OutgoingMessage], Awaitable[None]]
OnEdited = Callable[[MessageEdited], Awaitable[None]]
OnDraft = Callable[[DraftUpdate], Awaitable[None]]


class TelethonTelegramClient:
    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session: str,
        on_incoming: OnIncoming,
        on_outgoing: OnOutgoing,
        on_edited: OnEdited,
        on_draft: OnDraft,
    ) -> None:
        self._client = _Telethon(session, api_id, api_hash)
        self._on_incoming = on_incoming
        self._on_outgoing = on_outgoing
        self._on_edited = on_edited
        self._on_draft = on_draft

    async def connect(self) -> None:
        log.info("starting Telethon client (interactive login on first run)")
        await self._client.start()  # interactive login on first run
        me = await self._client.get_me()
        log.info(
            "logged in as id=%s username=%s phone=%s",
            getattr(me, "id", None), getattr(me, "username", None), getattr(me, "phone", None),
        )

        @self._client.on(_events.NewMessage(incoming=True))
        async def _(event):
            msg = await self._to_message(event)
            log.debug(
                "telethon NewMessage incoming chat=%s sender=%s id=%s text_len=%d",
                msg.chat_id, msg.sender, msg.message_id, len(msg.text),
            )
            await self._on_incoming(IncomingMessage(msg))

        @self._client.on(_events.NewMessage(outgoing=True))
        async def _out(event):
            msg = await self._to_message(event)
            log.debug(
                "telethon NewMessage outgoing chat=%s sender=%s id=%s text_len=%d",
                msg.chat_id, msg.sender, msg.message_id, len(msg.text),
            )
            await self._on_outgoing(OutgoingMessage(msg))

        @self._client.on(_events.MessageEdited(incoming=True))
        async def _edit(event):
            msg = await self._to_message(event)
            log.debug(
                "telethon MessageEdited incoming chat=%s sender=%s id=%s text_len=%d",
                msg.chat_id, msg.sender, msg.message_id, len(msg.text),
            )
            await self._on_edited(MessageEdited(msg))

        @self._client.on(_events.Raw())
        async def _raw(update):
            # Detect UpdateDraftMessage.
            if type(update).__name__ == "UpdateDraftMessage":
                try:
                    chat_id = self._peer_to_chat_id(update.peer)
                    text = getattr(update.draft, "message", "") or ""
                    log.debug(
                        "telethon UpdateDraftMessage chat=%s text_len=%d", chat_id, len(text)
                    )
                    await self._on_draft(DraftUpdate(chat_id=chat_id, text=text))
                except Exception as e:
                    log.warning("failed to translate draft update: %s", e)

    async def disconnect(self) -> None:
        log.info("disconnecting Telethon client")
        await self._client.disconnect()

    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        files: list[Path] | None = None,
    ) -> None:
        log.debug(
            "send_message chat=%s files=%d has_text=%s reply_to=%s",
            chat_id, len(files or []), text is not None, reply_to,
        )
        if files:
            await self._client.send_file(
                chat_id, file=[str(p) for p in files], caption=text or None, reply_to=reply_to
            )
        elif text is not None:
            await self._client.send_message(chat_id, text, reply_to=reply_to)

    async def write_draft(self, chat_id: int, text: str) -> None:
        log.debug("telethon SaveDraftRequest chat=%s text_len=%d", chat_id, len(text))
        peer = await self._client.get_input_entity(chat_id)
        await self._client(SaveDraftRequest(peer=peer, message=text))

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        log.debug(
            "telethon edit_message chat=%s id=%s text_len=%d",
            chat_id, message_id, len(text),
        )
        await self._client.edit_message(chat_id, message_id, text)

    async def fetch_history(self, chat_id: int, n: int) -> list[Message]:
        out: list[Message] = []
        async for m in self._client.iter_messages(chat_id, limit=n):
            out.append(
                Message(
                    chat_id=chat_id,
                    message_id=m.id,
                    sender=str(getattr(m.sender, "username", None) or m.sender_id or "unknown"),
                    timestamp=m.date.astimezone(timezone.utc),
                    text=m.message or "",
                    outgoing=bool(m.out),
                )
            )
        out.reverse()
        return out

    async def download_media(self, message_id: int, chat_id: int, dest_dir: Path) -> Path:
        messages = await self._client.get_messages(chat_id, ids=message_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = await self._client.download_media(messages, file=str(dest_dir) + "/")
        return Path(path)

    async def _to_message(self, event) -> Message:
        sender = await event.get_sender()
        return Message(
            chat_id=event.chat_id,
            message_id=event.message.id,
            sender=str(getattr(sender, "username", None) or event.sender_id or "unknown"),
            timestamp=event.message.date.astimezone(timezone.utc),
            text=event.message.message or "",
            outgoing=bool(event.message.out),
        )

    @staticmethod
    def _peer_to_chat_id(peer) -> int:
        return get_peer_id(peer)
