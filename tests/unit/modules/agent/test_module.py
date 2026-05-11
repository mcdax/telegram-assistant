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


# ---------- debounce scheduling ----------

async def test_on_draft_update_schedules_run_after_debounce(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    await mod.init(ctx)

    runs: list[tuple[int, str]] = []

    async def fake_run(chat_id: int) -> None:
        runs.append((chat_id, mod._pending_instruction.get(chat_id, "")))

    mod._run = fake_run  # type: ignore[assignment]
    match = MarkerMatch(
        module="agent", marker=mod.markers()[0], remainder="hello world",
    )
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent hello world"), match)
    await asyncio.sleep(0.2)
    assert runs == [(5, "hello world")]
    await http.close()


async def test_consecutive_draft_updates_cancel_and_replace(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    await mod.init(ctx)

    runs: list[tuple[int, str]] = []

    async def fake_run(chat_id: int) -> None:
        runs.append((chat_id, mod._pending_instruction.get(chat_id, "")))

    mod._run = fake_run  # type: ignore[assignment]
    marker = mod.markers()[0]
    match1 = MarkerMatch(module="agent", marker=marker, remainder="first")
    match2 = MarkerMatch(module="agent", marker=marker, remainder="second")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent first"), match1)
    await asyncio.sleep(0.01)
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent second"), match2)
    await asyncio.sleep(0.2)
    assert runs == [(5, "second")]
    await http.close()


async def test_plain_draft_update_cancels_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    await mod.init(ctx)

    runs: list[tuple[int, str]] = []

    async def fake_run(chat_id: int) -> None:
        runs.append((chat_id, mod._pending_instruction.get(chat_id, "")))

    mod._run = fake_run  # type: ignore[assignment]
    match = MarkerMatch(module="agent", marker=mod.markers()[0], remainder="x")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent x"), match)
    await asyncio.sleep(0.01)
    await mod.on_plain_draft_update(DraftUpdate(chat_id=5, text="other"))
    await asyncio.sleep(0.2)
    assert runs == []
    await http.close()


async def test_cancelled_task_does_not_pop_replacement(
    tmp_path: Path, monkeypatch
):
    """Regression: ``_cancel_pending`` is synchronous, so a cancelled task's
    ``finally`` runs AFTER the replacement is already in ``_pending``.
    The cancelled task must not clear the slot — otherwise a subsequent
    update would create a sibling instead of cancel-and-replace, racing
    the live task."""
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    # Bump the debounce window so the replacement task is reliably still
    # sleeping when we inspect ``_pending``.
    ctx.config["debounce_s"] = 0.5
    await mod.init(ctx)

    async def fake_run(chat_id: int) -> None:
        return

    mod._run = fake_run  # type: ignore[assignment]
    marker = mod.markers()[0]
    await mod.on_draft_update(
        DraftUpdate(chat_id=5, text="/agent first"),
        MarkerMatch(module="agent", marker=marker, remainder="first"),
    )
    first = mod._pending[5]
    await mod.on_draft_update(
        DraftUpdate(chat_id=5, text="/agent second"),
        MarkerMatch(module="agent", marker=marker, remainder="second"),
    )
    second = mod._pending[5]
    assert first is not second
    # Let the cancelled ``first`` task drain its finally block.
    await asyncio.sleep(0.05)
    # The replacement must still be registered.
    assert mod._pending.get(5) is second
    # And cancelling now must successfully cancel the *replacement*.
    assert mod._cancel_pending(5) is True
    await asyncio.sleep(0.1)
    await http.close()


async def test_shutdown_cancels_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    await mod.init(ctx)

    runs: list[int] = []

    async def fake_run(chat_id: int) -> None:
        runs.append(chat_id)

    mod._run = fake_run  # type: ignore[assignment]
    match = MarkerMatch(module="agent", marker=mod.markers()[0], remainder="x")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent x"), match)
    await asyncio.sleep(0.01)
    await mod.shutdown()
    await asyncio.sleep(0.2)
    assert runs == []
    await http.close()
