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

    mod._run_session = fake_run  # type: ignore[assignment]
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

    mod._run_session = fake_run  # type: ignore[assignment]
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

    mod._run_session = fake_run  # type: ignore[assignment]
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

    mod._run_session = fake_run  # type: ignore[assignment]
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


# ---------- run flow happy path ----------

async def test_run_clears_draft_calls_llm_and_sends_to_bot(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok-xyz")
    sent: list[dict] = []

    async def fake_send(http, token, *, chat_id: int, text: str) -> None:
        sent.append({"token": token, "chat_id": chat_id, "text": text})

    mod = AgentModule(send_text=fake_send)
    ctx, tg, _, http = await _ctx(tmp_path)
    from tests.fakes.telegram import make_message
    tg.seed_history(5, [
        make_message(5, "alice", "hi", message_id=1),
        make_message(5, "me", "hey", message_id=2, outgoing=True),
    ])
    tg.drafts[5] = "/agent summarize this"
    await mod.init(ctx)
    match = MarkerMatch(
        module="agent", marker=mod.markers()[0], remainder="summarize this",
    )
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent summarize this"), match)
    await asyncio.sleep(0.2)
    assert tg.drafts[5] == ""
    assert len(sent) == 1
    assert sent[0]["token"] == "tok-xyz"
    assert sent[0]["chat_id"] == 999
    assert sent[0]["text"] == "AGENT REPLY"
    await http.close()


# ---------- run flow: send-disabled + empty instruction ----------

async def test_run_send_disabled_runs_llm_and_logs_but_skips_bot(
    tmp_path: Path, monkeypatch, caplog
):
    """No bot token → LLM still runs; no bot send; output logged at INFO."""
    monkeypatch.delenv("AGENT_BOT_TOKEN", raising=False)
    sent: list[dict] = []

    async def fake_send(http, token, *, chat_id: int, text: str) -> None:
        sent.append({"chat_id": chat_id, "text": text})

    mod = AgentModule(send_text=fake_send)
    ctx, tg, _, http = await _ctx(tmp_path)
    from tests.fakes.telegram import make_message
    tg.seed_history(5, [make_message(5, "alice", "hi")])
    tg.drafts[5] = "/agent x"
    await mod.init(ctx)

    match = MarkerMatch(module="agent", marker=mod.markers()[0], remainder="x")
    with caplog.at_level(logging.INFO):
        await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent x"), match)
        await asyncio.sleep(0.2)

    assert tg.drafts[5] == ""
    assert sent == []
    assert any("AGENT REPLY" in r.message for r in caplog.records)
    await http.close()


async def test_run_empty_instruction_clears_draft_and_skips_llm(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    sent: list[dict] = []

    async def fake_send(http, token, *, chat_id: int, text: str) -> None:
        sent.append({"chat_id": chat_id, "text": text})

    mod = AgentModule(send_text=fake_send)
    ctx, tg, _, http = await _ctx(tmp_path)
    tg.drafts[5] = "/agent"
    await mod.init(ctx)

    match = MarkerMatch(module="agent", marker=mod.markers()[0], remainder="")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent"), match)
    await asyncio.sleep(0.2)

    assert tg.drafts[5] == ""
    assert sent == []
    await http.close()


# ---------- error + cancellation paths ----------

async def test_run_llm_exception_posts_error_to_bot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    sent: list[dict] = []

    async def fake_send(http, token, *, chat_id: int, text: str) -> None:
        sent.append({"chat_id": chat_id, "text": text})

    class BoomLLM:
        def agent(self, sp):
            return object()

        async def run(self, agent, user_text):
            raise RuntimeError("llm exploded")

    mod = AgentModule(send_text=fake_send)
    ctx, tg, _, http = await _ctx(tmp_path)
    ctx = ModuleContext(
        tg=ctx.tg, llm=BoomLLM(), http=ctx.http,           # type: ignore[arg-type]
        config=ctx.config, state=ctx.state, log=ctx.log,
    )
    from tests.fakes.telegram import make_message
    tg.seed_history(5, [make_message(5, "alice", "hi")])
    await mod.init(ctx)

    match = MarkerMatch(module="agent", marker=mod.markers()[0], remainder="x")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent x"), match)
    await asyncio.sleep(0.2)
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 999
    assert sent[0]["text"].startswith("❌ agent:")
    assert "llm exploded" in sent[0]["text"]
    await http.close()


async def test_run_cancelled_mid_llm_does_not_post_error(
    tmp_path: Path, monkeypatch
):
    """A second /agent update while the LLM is mid-call cancels the first
    run cleanly — no error posted, only the second run's output reaches
    the bot."""
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    sent: list[dict] = []
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def fake_send(http, token, *, chat_id: int, text: str) -> None:
        sent.append({"chat_id": chat_id, "text": text})

    class BlockingThenFastLLM:
        def __init__(self) -> None:
            self.calls = 0

        def agent(self, sp):
            return object()

        async def run(self, agent, user_text):
            self.calls += 1
            if self.calls == 1:
                started.set()
                await proceed.wait()
                return "FIRST"
            return "SECOND"

    mod = AgentModule(send_text=fake_send)
    ctx, tg, _, http = await _ctx(tmp_path)
    ctx = ModuleContext(
        tg=ctx.tg, llm=BlockingThenFastLLM(), http=ctx.http,  # type: ignore[arg-type]
        config=ctx.config, state=ctx.state, log=ctx.log,
    )
    from tests.fakes.telegram import make_message
    tg.seed_history(5, [make_message(5, "alice", "hi")])
    await mod.init(ctx)

    marker = mod.markers()[0]
    await mod.on_draft_update(
        DraftUpdate(chat_id=5, text="/agent first"),
        MarkerMatch(module="agent", marker=marker, remainder="first"),
    )
    await asyncio.wait_for(started.wait(), timeout=1.0)

    await mod.on_draft_update(
        DraftUpdate(chat_id=5, text="/agent second"),
        MarkerMatch(module="agent", marker=marker, remainder="second"),
    )

    proceed.set()
    await asyncio.sleep(0.3)

    assert [s["text"] for s in sent] == ["SECOND"]
    await http.close()


# ---------- openai-compatible backend ----------

async def test_init_uses_openai_drafter_when_block_configured(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    monkeypatch.setenv("AGENT_OAI_KEY", "sk-x")

    captured: dict[str, object] = {}

    class _CapturingDrafter:
        def __init__(self) -> None:
            self.calls = 0

        async def draft(self, *, chat_id, chat_title, history, instruction):
            self.calls += 1
            captured["chat_id"] = chat_id
            captured["instruction"] = instruction
            captured["history_len"] = len(history)
            return "OPENAI REPLY"

    drafter = _CapturingDrafter()
    sent: list[dict] = []

    async def fake_send(http, token, *, chat_id: int, text: str) -> None:
        sent.append({"chat_id": chat_id, "text": text})

    mod = AgentModule(send_text=fake_send)
    ctx, tg, _, http = await _ctx(
        tmp_path,
        config_overrides={
            "openai": {
                "base_url": "https://api.example/v1",
                "model": "test-model",
                "api_key_env": "AGENT_OAI_KEY",
                "instruction": "OAI INSTR",
            },
        },
    )
    await mod.init(ctx)
    mod._openai_drafter = drafter  # type: ignore[assignment]

    from tests.fakes.telegram import make_message
    tg.seed_history(5, [
        make_message(5, "alice", "hi"),
        make_message(5, "alice", "still here"),
    ])
    match = MarkerMatch(
        module="agent", marker=mod.markers()[0], remainder="do the thing",
    )
    await mod.on_draft_update(
        DraftUpdate(chat_id=5, text="/agent do the thing"), match,
    )
    await asyncio.sleep(0.2)

    assert drafter.calls == 1
    assert captured["chat_id"] == 5
    # The instruction now includes the CLARIFY/ANSWER protocol suffix
    assert captured["instruction"].startswith("do the thing")
    assert "CLARIFY:" in captured["instruction"]
    assert captured["history_len"] == 2
    assert sent == [{"chat_id": 999, "text": "OPENAI REPLY"}]
    await http.close()


async def test_shutdown_cancels_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")
    mod = AgentModule()
    ctx, _, _, http = await _ctx(tmp_path)
    await mod.init(ctx)

    runs: list[int] = []

    async def fake_run(chat_id: int) -> None:
        runs.append(chat_id)

    mod._run_session = fake_run  # type: ignore[assignment]
    match = MarkerMatch(module="agent", marker=mod.markers()[0], remainder="x")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/agent x"), match)
    await asyncio.sleep(0.01)
    await mod.shutdown()
    await asyncio.sleep(0.2)
    assert runs == []
    await http.close()
