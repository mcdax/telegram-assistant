from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from telegram_assistant.events import IncomingMessage
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.media_reply.backends import DownloadBackend, DownloadError
from telegram_assistant.modules.media_reply.module import MediaReplyModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


class FakeBackend:
    def __init__(self, path: Path | None = None, err: Exception | None = None) -> None:
        self._path = path
        self._err = err
        self.calls: list[str] = []

    async def download(self, url: str, dest_dir: Path) -> Path:
        self.calls.append(url)
        if self._err:
            raise self._err
        assert self._path is not None
        dest = dest_dir / self._path.name
        dest.write_bytes(b"x")
        return dest


async def _ctx(tmp_path: Path, config: dict[str, Any], backend: FakeBackend) -> ModuleContext:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    ctx = ModuleContext(
        tg=tg,
        llm=fake_llm(""),
        http=http,
        config=config,
        state=state.for_module("media_reply"),
        log=logging.getLogger("mr"),
    )
    # Inject the backend so tests are hermetic.
    MediaReplyModule._backend_override = backend  # type: ignore[attr-defined]
    return ctx


def _instagram_cfg(chats: list[int]) -> dict[str, Any]:
    return {
        "enabled": True,
        "chats": chats,
        "send_as": "reply",
        "download_timeout_s": 5,
        "handlers": [
            {
                "name": "instagram",
                "pattern": r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[\w-]+",
                "backend": "yt_dlp",
            }
        ],
    }


async def test_whitelisted_match_triggers_download(tmp_path: Path):
    backend = FakeBackend(path=Path("video.mp4"))
    ctx = await _ctx(tmp_path, _instagram_cfg([42]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(
            make_message(42, "alice", "look https://instagram.com/reel/abc123", message_id=9)
        )
    )
    assert backend.calls == ["https://instagram.com/reel/abc123"]
    assert len(ctx.tg.sent) == 1  # type: ignore[attr-defined]
    sent = ctx.tg.sent[0]  # type: ignore[attr-defined]
    assert sent.chat_id == 42
    assert sent.reply_to == 9
    await ctx.http.close()


async def test_not_whitelisted_no_action(tmp_path: Path):
    backend = FakeBackend(path=Path("video.mp4"))
    ctx = await _ctx(tmp_path, _instagram_cfg([1]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(42, "alice", "https://instagram.com/reel/abc"))
    )
    assert backend.calls == []
    assert ctx.tg.sent == []  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_no_match_no_action(tmp_path: Path):
    backend = FakeBackend(path=Path("video.mp4"))
    ctx = await _ctx(tmp_path, _instagram_cfg([1]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "no link here")))
    assert backend.calls == []
    await ctx.http.close()


async def test_backend_failure_logged_no_reply(tmp_path: Path, caplog):
    backend = FakeBackend(err=DownloadError("boom"))
    ctx = await _ctx(tmp_path, _instagram_cfg([1]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(1, "alice", "https://instagram.com/p/xyz"))
    )
    assert ctx.tg.sent == []  # type: ignore[attr-defined]
    assert any("boom" in r.message for r in caplog.records)
    await ctx.http.close()


async def test_first_handler_wins(tmp_path: Path):
    backend = FakeBackend(path=Path("clip.mp4"))
    cfg = _instagram_cfg([1])
    cfg["handlers"].append(
        {"name": "tiktok", "pattern": r"tiktok\.com", "backend": "yt_dlp"}
    )
    ctx = await _ctx(tmp_path, cfg, backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    # Message has both an instagram and a tiktok link; instagram is first in config.
    await mod.on_incoming_message(
        IncomingMessage(
            make_message(
                1, "alice", "x https://instagram.com/p/1 y https://tiktok.com/@u/video/1"
            )
        )
    )
    assert backend.calls == ["https://instagram.com/p/1"]
    await ctx.http.close()
