"""Media-reply module. Matches URL regexes in incoming messages, downloads, replies.

Per-chat toggling:
  /auto_media on|off — enable / disable URL-aware replies for the current chat.
The config's ``chats`` list seeds the initial whitelist; the runtime truth
lives in state.toml under ``[media_reply.auto_media]`` and is updated by
the markers.
"""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram_assistant.events import DraftUpdate, IncomingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.state import StateWriteError

from .backends import DownloadBackend, DownloadError, get_backend


DEFAULT_MARKERS = {
    "auto_media_on": "/auto_media on",
    "auto_media_off": "/auto_media off",
}
_AUTO_MEDIA_BUCKET = "auto_media"


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
        self._chats_seed: set[int] = set()
        self._markers: list[Marker] = []

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        self._chats_seed = {int(c) for c in cfg.get("chats", [])}
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

        user_markers = cfg.get("markers", {})

        def trigger(key: str) -> str:
            return user_markers.get(key, DEFAULT_MARKERS[key])

        self._markers = [
            Marker(
                name="auto_media_on", trigger=trigger("auto_media_on"),
                kind=MatchKind.EXACT, priority=100,
            ),
            Marker(
                name="auto_media_off", trigger=trigger("auto_media_off"),
                kind=MatchKind.EXACT, priority=100,
            ),
        ]

    async def shutdown(self) -> None:
        return

    def markers(self) -> list[Marker]:
        return list(self._markers)

    async def on_draft_update(self, event: DraftUpdate, match: MarkerMatch) -> None:
        assert self._ctx is not None
        name = match.marker.name
        self._ctx.log.debug("on_draft_update chat=%s marker=%s", event.chat_id, name)
        if name == "auto_media_on":
            await self._set_toggle(event.chat_id, True)
        elif name == "auto_media_off":
            await self._set_toggle(event.chat_id, False)

    async def on_incoming_message(self, event: IncomingMessage) -> None:
        assert self._ctx is not None
        msg = event.message
        if not self._auto_on(msg.chat_id):
            self._ctx.log.debug(
                "skip incoming chat=%s: media_reply not enabled", msg.chat_id
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

    def _auto_on(self, chat_id: int) -> bool:
        assert self._ctx is not None
        override = self._ctx.state.get(_AUTO_MEDIA_BUCKET, str(chat_id), default=None)
        if override is not None:
            return bool(override)
        return chat_id in self._chats_seed

    async def _set_toggle(self, chat_id: int, on: bool) -> None:
        assert self._ctx is not None
        try:
            self._ctx.state.set(_AUTO_MEDIA_BUCKET, str(chat_id), on)
            word = "enabled" if on else "disabled"
            text = f"✓ Media-reply {word} for this chat"
        except StateWriteError:
            text = "✗ state write failed"
        await self._ctx.tg.write_draft(chat_id, text)
