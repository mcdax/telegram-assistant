"""Tests for the OpenAI-compatible drafting backend.

We test:
  * load_openai_config — env-var resolution, optional system_prompt
  * build_payload       — structured message array with attachments + is_me
  * OpenAIDrafter.draft — feeds payload through an LLMFactory (TestModel)

No real provider construction or network traffic.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from telegram_assistant.events import Attachment, Message
from telegram_assistant.llm import LLMFactory
from telegram_assistant.modules.drafting.openai_drafter import (
    OpenAIConfig,
    OpenAIDrafter,
    build_payload,
    load_openai_config,
)
from tests.fakes.llm import fake_llm


def _msg(
    *,
    chat_id: int,
    sender: str,
    text: str,
    outgoing: bool,
    sender_id: int | None = None,
    message_type: str = "text",
    attachment: Attachment | None = None,
    message_id: int = 1,
    ts: str = "2026-04-23T18:00:00+00:00",
) -> Message:
    return Message(
        chat_id=chat_id,
        message_id=message_id,
        sender=sender,
        timestamp=datetime.fromisoformat(ts),
        text=text,
        outgoing=outgoing,
        sender_id=sender_id,
        message_type=message_type,
        attachment=attachment,
    )


# ---------- load_openai_config ----------


def test_config_none_when_section_missing():
    assert load_openai_config(None, fallback_system_prompt="fb") is None
    assert load_openai_config({}, fallback_system_prompt="fb") is None


def test_config_none_when_any_field_missing(monkeypatch):
    monkeypatch.setenv("KEY_A", "secret")
    assert load_openai_config(
        {"base_url": "x", "api_key_env": "KEY_A"}, fallback_system_prompt="fb"
    ) is None
    assert load_openai_config(
        {"model": "m", "api_key_env": "KEY_A"}, fallback_system_prompt="fb"
    ) is None
    assert load_openai_config(
        {"base_url": "x", "model": "m"}, fallback_system_prompt="fb"
    ) is None


def test_config_none_when_env_var_unset(monkeypatch, caplog):
    monkeypatch.delenv("MISSING_KEY", raising=False)
    cfg = load_openai_config(
        {"base_url": "x", "model": "m", "api_key_env": "MISSING_KEY"},
        fallback_system_prompt="fb",
    )
    assert cfg is None
    assert any("MISSING_KEY" in rec.message for rec in caplog.records)


def test_config_uses_section_system_prompt_when_present(monkeypatch):
    monkeypatch.setenv("KEY", "sk-x")
    cfg = load_openai_config(
        {
            "base_url": "https://api.example/v1",
            "model": "m1",
            "api_key_env": "KEY",
            "system_prompt": "custom sp",
        },
        fallback_system_prompt="fallback sp",
    )
    assert cfg is not None
    assert cfg.system_prompt == "custom sp"
    assert cfg.api_key == "sk-x"
    assert cfg.base_url == "https://api.example/v1"
    assert cfg.model == "m1"


def test_config_falls_back_to_default_system_prompt(monkeypatch):
    monkeypatch.setenv("KEY", "sk-x")
    cfg = load_openai_config(
        {"base_url": "b", "model": "m", "api_key_env": "KEY"},
        fallback_system_prompt="fallback sp",
    )
    assert cfg is not None
    assert cfg.system_prompt == "fallback sp"


# ---------- build_payload ----------


def test_payload_includes_chat_id_and_title():
    payload = build_payload(
        chat_id=42, chat_title="alice", history=[], instruction="",
    )
    assert payload["chat_id"] == 42
    assert payload["chat_title"] == "alice"
    assert payload["messages"] == []
    assert payload["instruction"] is None


def test_payload_per_message_structure():
    history = [
        _msg(
            chat_id=42, sender="alice", text="hi", outgoing=False,
            sender_id=1001, message_id=1, ts="2026-04-23T10:00:00+00:00",
        ),
        _msg(
            chat_id=42, sender="me", text="hello", outgoing=True,
            sender_id=500, message_id=2, ts="2026-04-23T10:01:00+00:00",
        ),
    ]
    payload = build_payload(
        chat_id=42, chat_title="alice", history=history, instruction="keep short",
    )
    assert payload["instruction"] == "keep short"
    assert len(payload["messages"]) == 2
    assert payload["messages"][0] == {
        "time": "2026-04-23T10:00:00+00:00",
        "chat_id": 42,
        "sender": "alice",
        "sender_id": 1001,
        "is_me": False,
        "type": "text",
        "text": "hi",
        "attachment": None,
    }
    assert payload["messages"][1]["is_me"] is True
    assert payload["messages"][1]["sender_id"] == 500


def test_payload_serialises_attachment():
    history = [
        _msg(
            chat_id=7, sender="bob", text="", outgoing=False,
            message_type="voice",
            attachment=Attachment(type="voice", description="voice 12s", url=None),
        ),
        _msg(
            chat_id=7, sender="bob", text="", outgoing=False,
            message_type="weblink",
            attachment=Attachment(
                type="weblink",
                description="link: an article",
                url="https://example.com/foo",
            ),
        ),
    ]
    payload = build_payload(chat_id=7, chat_title="bob", history=history, instruction="")
    assert payload["messages"][0]["attachment"] == {
        "type": "voice", "description": "voice 12s", "url": None,
    }
    assert payload["messages"][1]["attachment"] == {
        "type": "weblink",
        "description": "link: an article",
        "url": "https://example.com/foo",
    }


# ---------- OpenAIDrafter.draft (TestModel-backed factory) ----------


async def test_draft_returns_llm_output():
    factory = fake_llm("generated reply")
    drafter = OpenAIDrafter(factory=factory, system_prompt="sys")
    history = [
        _msg(chat_id=9, sender="alice", text="hi", outgoing=False, sender_id=1),
    ]
    out = await drafter.draft(
        chat_id=9, chat_title="alice", history=history, instruction="short",
    )
    assert out == "generated reply"


async def test_draft_passes_json_payload_as_user_text(monkeypatch):
    """The user text sent to the LLM is the structured JSON payload."""
    captured: dict[str, str] = {}

    class _CapturingFactory:
        def agent(self, system_prompt: str):
            captured["system_prompt"] = system_prompt
            return "agent-sentinel"

        async def run(self, agent, user_text: str) -> str:  # noqa: ARG002
            captured["user_text"] = user_text
            return "OK"

    drafter = OpenAIDrafter(factory=_CapturingFactory(), system_prompt="sys-text")  # type: ignore[arg-type]
    history = [
        _msg(chat_id=9, sender="alice", text="hi", outgoing=False, sender_id=1),
        _msg(chat_id=9, sender="me", text="hey", outgoing=True, message_id=2),
    ]
    await drafter.draft(
        chat_id=9, chat_title="alice", history=history, instruction="keep it short",
    )
    assert captured["system_prompt"] == "sys-text"
    payload = json.loads(captured["user_text"])
    assert payload["chat_id"] == 9
    assert payload["chat_title"] == "alice"
    assert payload["instruction"] == "keep it short"
    assert [m["is_me"] for m in payload["messages"]] == [False, True]
    assert payload["messages"][0]["sender_id"] == 1


def test_from_config_builds_factory_with_openai_provider(monkeypatch):
    """Smoke: from_config returns a drafter with a working LLMFactory,
    no network call required (we only build, not run)."""
    cfg = OpenAIConfig(
        base_url="https://api.example/v1",
        api_key="sk-test",
        model="gpt-4o-mini",
        system_prompt="sp",
    )
    drafter = OpenAIDrafter.from_config(cfg, timeout_s=5)
    assert drafter.system_prompt == "sp"
    # Sanity: the internal factory is an LLMFactory (duck-typed via agent method)
    assert hasattr(drafter, "_factory")
    assert hasattr(drafter._factory, "agent")  # type: ignore[attr-defined]
