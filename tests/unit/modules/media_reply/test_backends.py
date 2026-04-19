from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_assistant.modules.media_reply.backends import (
    DownloadError,
    YtDlpBackend,
    get_backend,
)


def test_get_backend_known():
    assert isinstance(get_backend("yt_dlp", timeout_s=10), YtDlpBackend)


def test_get_backend_unknown():
    with pytest.raises(KeyError):
        get_backend("nope", timeout_s=10)


async def test_yt_dlp_backend_success(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=10)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        # Simulate yt-dlp producing one output file.
        (tmp_path / "out.mp4").write_bytes(b"video")
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await backend.download("https://example.com/x", tmp_path)
    assert result.name == "out.mp4"


async def test_yt_dlp_backend_nonzero_exit(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=10)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b"error"))
        proc.returncode = 1
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DownloadError):
        await backend.download("https://example.com/x", tmp_path)


async def test_yt_dlp_backend_timeout(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=0)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        async def slow():
            await asyncio.sleep(5)
            return (b"", b"")
        proc.communicate = AsyncMock(side_effect=slow)
        proc.kill = MagicMock()
        proc.returncode = None
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DownloadError):
        await backend.download("https://example.com/x", tmp_path)


async def test_yt_dlp_backend_no_output_file(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=10)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        # no files created
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DownloadError):
        await backend.download("https://example.com/x", tmp_path)
