"""Tests for the agent module's multi-turn clarification feature."""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
import pytest

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate, OutgoingMessage
from tests.fakes.llm import LLMFactory
from tests.fakes.telegram import FakeTelegramClient, make_message


class SequencedLLM(LLMFactory):
    """LLMFactory that returns canned responses in sequence."""

    def __init__(self, responses: list[str], *, timeout_s: int = 5) -> None:
        from pydantic_ai.models.test import TestModel
        super().__init__(model=TestModel(custom_output_text="placeholder"), timeout_s=timeout_s)
        self._responses = list(responses)
        self._index = 0
        self.calls: list[str] = []

    async def run(self, agent, user_text: str) -> str:  # type: ignore[override]
        self.calls.append(user_text)
        if self._index >= len(self._responses):
            raise RuntimeError("SequencedLLM exhausted: no more responses")
        resp = self._responses[self._index]
        self._index += 1
        # We need to return the text directly since TestModel is a stub
        return resp


def _make_app(
    tmp_path: Path,
    monkeypatch,
    tg: FakeTelegramClient,
    llm: LLMFactory,
    sent: list[dict],
    *,
    clarify_timeout_s: float = 300,
    max_clarify_rounds: int = 3,
) -> App:
    """Build an App with the agent module and a fake send_text that
    records calls and returns incrementing message_ids."""
    monkeypatch.setenv("AGENT_BOT_TOKEN", "tok")

    _msg_id_counter = [100]

    async def fake_send(http_, token, *, chat_id: int, text: str) -> int:
        _msg_id_counter[0] += 1
        mid = _msg_id_counter[0]
        sent.append({"token": token, "chat_id": chat_id, "text": text, "message_id": mid})
        return mid

    import telegram_assistant.modules.agent.module as agent_module
    monkeypatch.setattr(agent_module._bot_sender, "send_text", fake_send)

    modules_cfg = {
        "agent": {
            "enabled": True,
            "debounce_s": 0.05,
            "last_n": 5,
            "default_system_prompt": "",
            "bot_token_env": "AGENT_BOT_TOKEN",
            "target_chat_id": 7777,
            "clarify_timeout_s": clarify_timeout_s,
            "max_clarify_rounds": max_clarify_rounds,
            "markers": {},
        },
    }
    http = aiohttp.ClientSession()
    app = App(tg=tg, llm=llm, http=http, state_path=tmp_path / "state.toml")
    # Store config for later start
    app._test_modules_cfg = modules_cfg  # type: ignore[attr-defined]
    return app


async def test_clarify_then_answer(tmp_path: Path, monkeypatch):
    """LLM asks CLARIFY first, user replies, LLM gives ANSWER."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    sent: list[dict] = []

    llm = SequencedLLM([
        "CLARIFY: Which weekend?",
        "ANSWER: Got it — I'll tell Mara about this weekend.",
    ])
    app = _make_app(tmp_path, monkeypatch, tg, llm, sent)
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    # Trigger /agent
    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent tell mara"))
    await asyncio.sleep(0.3)

    # At this point the CLARIFY question should have been posted
    assert len(sent) == 1
    assert sent[0]["text"].startswith("❓")
    assert "Which weekend" in sent[0]["text"]

    # Simulate user reply in the bot chat (plain message, no reply-to)
    reply_msg = make_message(7777, "philipp", "this weekend", message_id=200, outgoing=True)
    await app.inject_outgoing(OutgoingMessage(message=reply_msg))
    await asyncio.sleep(0.3)

    # Now the final answer should have been posted
    assert len(sent) == 2
    assert sent[1]["text"].startswith("ANSWER:") or "tell Mara" in sent[1]["text"]

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]


async def test_direct_answer_no_clarify(tmp_path: Path, monkeypatch):
    """LLM responds with ANSWER immediately — no clarification round."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    sent: list[dict] = []

    llm = SequencedLLM(["ANSWER: Done!"])
    app = _make_app(tmp_path, monkeypatch, tg, llm, sent)
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent do something"))
    await asyncio.sleep(0.3)

    # Only one message — the answer
    assert len(sent) == 1
    assert "Done" in sent[0]["text"]

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]


async def test_max_rounds_exceeded(tmp_path: Path, monkeypatch):
    """LLM keeps asking CLARIFY — should stop after max_clarify_rounds."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    sent: list[dict] = []

    llm = SequencedLLM([
        "CLARIFY: Question 1?",
        "CLARIFY: Question 2?",
        "CLARIFY: Question 3?",
        "CLARIFY: Question 4?",  # should never be reached
    ])
    app = _make_app(
        tmp_path, monkeypatch, tg, llm, sent,
        max_clarify_rounds=2,
    )
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent ambiguous"))
    await asyncio.sleep(0.3)

    # Round 1: CLARIFY posted
    assert len(sent) == 1
    assert sent[0]["text"].startswith("❓")

    # Reply to round 1
    await app.inject_outgoing(OutgoingMessage(
        message=make_message(7777, "philipp", "answer 1", message_id=201, outgoing=True)
    ))
    await asyncio.sleep(0.3)

    # Round 2: CLARIFY posted (rounds=2, still within max)
    assert len(sent) == 2

    # Reply to round 2
    await app.inject_outgoing(OutgoingMessage(
        message=make_message(7777, "philipp", "answer 2", message_id=202, outgoing=True)
    ))
    await asyncio.sleep(0.3)

    # Round 3 would exceed max_clarify_rounds=2 → warning posted
    assert len(sent) == 3
    assert "Max clarification" in sent[2]["text"]

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]


async def test_timeout(tmp_path: Path, monkeypatch):
    """User doesn't reply — session should time out."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    sent: list[dict] = []

    llm = SequencedLLM(["CLARIFY: What timeframe?"])
    app = _make_app(
        tmp_path, monkeypatch, tg, llm, sent,
        clarify_timeout_s=0.15,
    )
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent schedule"))
    await asyncio.sleep(0.3)

    # CLARIFY posted, then timeout message
    assert len(sent) == 2
    assert sent[0]["text"].startswith("❓")
    assert "timeout" in sent[1]["text"].lower()

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]


async def test_reply_to_matching(tmp_path: Path, monkeypatch):
    """User replies to the specific CLARIFY message (Option B matching)."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    sent: list[dict] = []

    llm = SequencedLLM([
        "CLARIFY: Which day?",
        "ANSWER: Noted for Friday.",
    ])
    app = _make_app(tmp_path, monkeypatch, tg, llm, sent)
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent set meeting"))
    await asyncio.sleep(0.3)

    # CLARIFY posted with a message_id
    assert len(sent) == 1
    clarify_msg_id = sent[0]["message_id"]
    assert clarify_msg_id is not None

    # User replies TO that specific message
    reply_msg = make_message(
        7777, "philipp", "Friday", message_id=300, outgoing=True,
        reply_to_message_id=clarify_msg_id,
    )
    await app.inject_outgoing(OutgoingMessage(message=reply_msg))
    await asyncio.sleep(0.3)

    # Final answer posted
    assert len(sent) == 2
    assert "Friday" in sent[1]["text"] or "ANSWER" in sent[1]["text"]

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]


async def test_outgoing_wrong_chat_ignored(tmp_path: Path, monkeypatch):
    """Outgoing messages from a different chat should be ignored."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    sent: list[dict] = []

    llm = SequencedLLM(["CLARIFY: Which day?"])
    app = _make_app(
        tmp_path, monkeypatch, tg, llm, sent,
        clarify_timeout_s=2.0,
    )
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent test"))
    await asyncio.sleep(0.3)

    # CLARIFY posted
    assert len(sent) == 1

    # User sends a message in a DIFFERENT chat (not the bot chat)
    other_msg = make_message(9999, "philipp", "hello there", message_id=400, outgoing=True)
    await app.inject_outgoing(OutgoingMessage(message=other_msg))
    await asyncio.sleep(0.1)

    # No additional bot posts — the other-chat message was ignored
    assert len(sent) == 1

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]


async def test_new_agent_cancels_active_session(tmp_path: Path, monkeypatch):
    """A new /agent while a clarification session is active cancels it."""
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    tg.seed_history(99, [make_message(99, "bob", "yo")])
    sent: list[dict] = []

    llm = SequencedLLM([
        "CLARIFY: Which day?",       # first /agent
        "ANSWER: New task done.",    # second /agent
    ])
    app = _make_app(
        tmp_path, monkeypatch, tg, llm, sent,
        clarify_timeout_s=5.0,
    )
    await app.start(app._test_modules_cfg)  # type: ignore[attr-defined]

    # First /agent
    await app.inject_draft_update(DraftUpdate(chat_id=42, text="/agent first task"))
    await asyncio.sleep(0.3)
    assert len(sent) == 1  # CLARIFY posted

    # Second /agent in a different chat — cancels first session
    await app.inject_draft_update(DraftUpdate(chat_id=99, text="/agent second task"))
    await asyncio.sleep(0.3)
    assert len(sent) == 2  # ANSWER for second task posted

    # The first session's future should have been cancelled — a late
    # reply in the bot chat should not cause issues
    late_reply = make_message(7777, "philipp", "Friday", message_id=500, outgoing=True)
    await app.inject_outgoing(OutgoingMessage(message=late_reply))
    await asyncio.sleep(0.1)

    # No additional posts from the cancelled session
    assert len(sent) == 2

    await app.stop()
    await app._http.close()  # type: ignore[attr-defined]