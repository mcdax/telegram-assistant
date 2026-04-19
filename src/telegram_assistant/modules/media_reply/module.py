"""Media-reply module. Matches URL regexes in incoming messages, downloads, replies."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram_assistant.events import IncomingMessage
from telegram_assistant.module import ModuleContext

from .backends import DownloadBackend, DownloadError, get_backend


@dataclass
class Handler:
    name: str
    pattern: re.Pattern[str]
    backend: DownloadBackend


class MediaReplyModule:
    name = "media_reply"

    _backend_override: DownloadBackend | None = None  # test hook

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._handlers: list[Handler] = []
        self._chats: set[int] = set()

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        self._chats = {int(c) for c in cfg.get("chats", [])}
        timeout_s = int(cfg.get("download_timeout_s", 60))
        self._handlers = []
        for h in cfg.get("handlers", []):
            backend: DownloadBackend = (
                MediaReplyModule._backend_override
                if MediaReplyModule._backend_override is not None
                else get_backend(h["backend"], timeout_s=timeout_s)
            )
            self._handlers.append(
                Handler(
                    name=h["name"],
                    pattern=re.compile(h["pattern"]),
                    backend=backend,
                )
            )

    async def shutdown(self) -> None:
        return

    def markers(self):
        return []

    async def on_incoming_message(self, event: IncomingMessage) -> None:
        assert self._ctx is not None
        msg = event.message
        if msg.chat_id not in self._chats:
            self._ctx.log.debug(
                "skip incoming chat=%s: not in media_reply whitelist", msg.chat_id
            )
            return
        match_url: str | None = None
        picked: Handler | None = None
        for h in self._handlers:
            m = h.pattern.search(msg.text)
            if m:
                match_url = m.group(0)
                picked = h
                break
        if picked is None or match_url is None:
            self._ctx.log.debug(
                "no URL handler matched incoming in chat=%s", msg.chat_id
            )
            return

        self._ctx.log.debug(
            "handler=%s matched url=%s chat=%s", picked.name, match_url, msg.chat_id
        )
        with tempfile.TemporaryDirectory(prefix="tga-media-") as td:
            td_path = Path(td)
            self._ctx.log.debug("download starting handler=%s url=%s", picked.name, match_url)
            try:
                file_path = await picked.backend.download(match_url, td_path)
            except DownloadError as e:
                self._ctx.log.warning("download failed (%s): %s", picked.name, e)
                return
            size = file_path.stat().st_size if file_path.exists() else -1
            self._ctx.log.debug(
                "download complete file=%s bytes=%d — sending reply", file_path.name, size
            )
            await self._ctx.tg.send_message(
                chat_id=msg.chat_id,
                reply_to=msg.message_id,
                files=[file_path],
            )
