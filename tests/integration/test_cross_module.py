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


async def test_drafting_and_media_reply_both_fire(tmp_path: Path):
    backend = StubBackend()
    MediaReplyModule._backend_override = backend  # type: ignore[attr-defined]
    try:
        tg = FakeTelegramClient()
        tg.seed_history(
            42, [make_message(42, "alice", "hi there https://instagram.com/reel/xyz", message_id=7)]
        )
        http = aiohttp.ClientSession()
        modules_cfg = {
            "drafting": {
                "enabled": True,
                "default_system_prompt": "SP",
                "last_n": 5,
                "auto_draft_chats": [42],
                "enrichment_url": "",
                "markers": {},
            },
            "media_reply": {
                "enabled": True,
                "chats": [42],
                "send_as": "reply",
                "download_timeout_s": 5,
                "handlers": [
                    {
                        "name": "instagram",
                        "pattern": r"https?://instagram\.com/\S+",
                        "backend": "yt_dlp",
                    }
                ],
            },
        }
        app = App(tg=tg, llm=fake_llm("DRAFTED"), http=http, state_path=tmp_path / "state.toml")
        await app.start(modules_cfg)
        await app.inject_incoming(
            IncomingMessage(make_message(42, "alice", "hi there https://instagram.com/reel/xyz", message_id=7))
        )
        await app.drain()
        # Both fired:
        assert tg.drafts[42] == "DRAFTED"
        assert len(tg.sent) == 1
        assert tg.sent[0].reply_to == 7
        assert backend.calls == ["https://instagram.com/reel/xyz"]
        await app.stop()
        await http.close()
    finally:
        MediaReplyModule._backend_override = None  # type: ignore[attr-defined]
