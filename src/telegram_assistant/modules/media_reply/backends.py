"""Download backends for media_reply."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol


class DownloadError(RuntimeError):
    pass


class DownloadBackend(Protocol):
    async def download(self, url: str, dest_dir: Path) -> Path: ...


class YtDlpBackend:
    def __init__(self, timeout_s: int) -> None:
        self._timeout = timeout_s

    async def download(self, url: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        before = set(dest_dir.iterdir())
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--restrict-filenames",
            "-o",
            str(dest_dir / "%(title).80B.%(ext)s"),
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise DownloadError(f"yt-dlp timed out after {self._timeout}s") from None

        if proc.returncode != 0:
            raise DownloadError(
                f"yt-dlp exited {proc.returncode}: {stderr.decode(errors='replace')}"
            )

        after = set(dest_dir.iterdir())
        new = after - before
        if not new:
            raise DownloadError("yt-dlp produced no output file")
        # Return the newest file.
        return max(new, key=lambda p: p.stat().st_mtime)


_BACKENDS: dict[str, type] = {"yt_dlp": YtDlpBackend}


def get_backend(name: str, *, timeout_s: int) -> DownloadBackend:
    cls = _BACKENDS[name]
    return cls(timeout_s=timeout_s)
