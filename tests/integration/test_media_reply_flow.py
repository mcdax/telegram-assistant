from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import IncomingMessage
from telegram_assistant.modules.media_reply.module import MediaReplyModule
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


class StubBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def download(self, url: str, dest_dir: Path) -> Path:
        self.calls.append(url)
        p = dest_dir / "clip.mp4"
        p.write_bytes(b"video")
        return p


async def test_media_reply_end_to_end(tmp_path: Path):
    backend = StubBackend()
    MediaReplyModule._backend_override = backend  # type: ignore[attr-defined]
    tg = FakeTelegramClient()
    http = aiohttp.ClientSession()
    modules_cfg = {
        "media_reply": {
            "enabled": True,
            "chats": [5],
            "send_as": "reply",
            "download_timeout_s": 5,
            "handlers": [
                {
                    "name": "instagram",
                    "pattern": r"https?://instagram\.com/\S+",
                    "backend": "yt_dlp",
                }
            ],
        }
    }
    app = App(tg=tg, llm=fake_llm(""), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_incoming(
        IncomingMessage(make_message(5, "alice", "look https://instagram.com/reel/xyz", message_id=11))
    )
    await app.drain()
    assert backend.calls == ["https://instagram.com/reel/xyz"]
    assert len(tg.sent) == 1
    assert tg.sent[0].reply_to == 11
    await app.stop()
    await http.close()
    MediaReplyModule._backend_override = None  # type: ignore[attr-defined]
