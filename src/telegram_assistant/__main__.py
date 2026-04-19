"""CLI entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import aiohttp
from watchfiles import awatch

from .app import App
from .config import load_config
from .llm import LLMFactory
from .telethon_client import TelethonTelegramClient


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="telegram-assistant")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--state", type=Path, default=Path("state.toml"))
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=args.log_level.upper())
    cfg = load_config(args.config)
    http = aiohttp.ClientSession()

    app_holder: dict[str, App] = {}

    async def on_incoming(event):
        await app_holder["app"].inject_incoming(event)

    async def on_draft(event):
        await app_holder["app"].inject_draft_update(event)

    tg = TelethonTelegramClient(
        api_id=cfg.telegram.api_id,
        api_hash=cfg.telegram.api_hash,
        session=cfg.telegram.session,
        on_incoming=on_incoming,
        on_draft=on_draft,
    )
    llm = LLMFactory(model=cfg.llm.model, timeout_s=cfg.llm.timeout_s)

    app = App(tg=tg, llm=llm, http=http, state_path=args.state)
    app_holder["app"] = app

    async def watch_config() -> None:
        async for _changes in awatch(str(args.config)):
            try:
                new_cfg = load_config(args.config)
            except Exception as e:
                logging.warning("config reload failed: %s", e)
                continue
            logging.info("config reloaded (modules may require restart to apply)")
            _ = new_cfg

    try:
        async with asyncio.TaskGroup() as tg_group:
            tg_group.create_task(app.run(cfg.modules))
            tg_group.create_task(watch_config())
    finally:
        await http.close()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
