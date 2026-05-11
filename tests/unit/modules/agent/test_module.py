"""Tests for the agent module orchestration."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiohttp
import pytest

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.agent.module import AgentModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def _ctx(
    tmp_path: Path,
    *,
    config_overrides: dict | None = None,
) -> tuple[ModuleContext, FakeTelegramClient, RuntimeState, aiohttp.ClientSession]:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    config = {
        "enabled": True,
        "debounce_s": 0.05,            # tiny so tests don't drag
        "last_n": 5,
        "default_system_prompt": "SP",
        "bot_token_env": "AGENT_BOT_TOKEN",
        "target_chat_id": 999,
        "markers": {},
    }
    if config_overrides:
        config.update(config_overrides)
    ctx = ModuleContext(
        tg=tg,
        llm=fake_llm("AGENT REPLY"),
        http=http,
        config=config,
        state=state.for_module("agent"),
        log=logging.getLogger("a"),
    )
    return ctx, tg, state, http


# ---------- init / markers ----------

async def test_init_default_marker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    await mod.init(ctx)
    triggers = [m.trigger for m in mod.markers()]
    assert triggers == ["/agent"]
    assert mod.markers()[0].kind is MatchKind.CONTAINS
    await http.close()


async def test_init_custom_marker(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(
        tmp_path, config_overrides={"markers": {"agent": "!a"}}
    )
    await mod.init(ctx)
    assert mod.markers()[0].trigger == "!a"
    await http.close()


async def test_init_raises_on_zero_target_chat_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path, config_overrides={"target_chat_id": 0})
    with pytest.raises(ValueError, match="target_chat_id"):
        await mod.init(ctx)
    await http.close()


async def test_init_send_disabled_when_bot_token_env_unset(
    tmp_path: Path, monkeypatch, caplog
):
    monkeypatch.delenv("AGENT_BOT_TOKEN", raising=False)
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    with caplog.at_level(logging.WARNING):
        await mod.init(ctx)
    # Marker still registered.
    assert [m.trigger for m in mod.markers()] == ["/agent"]
    # Module flagged send-disabled.
    assert mod.send_disabled is True
    assert any("AGENT_BOT_TOKEN" in r.message for r in caplog.records)
    await http.close()


async def test_init_send_disabled_when_bot_token_env_field_missing(
    tmp_path: Path, monkeypatch, caplog
):
    """No `bot_token_env` in config at all — same soft path as missing env var."""
    monkeypatch.delenv("AGENT_BOT_TOKEN", raising=False)
    mod = AgentModule()
    ctx, _, _, http = await _ctx(
        tmp_path, config_overrides={"bot_token_env": ""}
    )
    with caplog.at_level(logging.WARNING):
        await mod.init(ctx)
    assert mod.send_disabled is True
    await http.close()
