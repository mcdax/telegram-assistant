from __future__ import annotations

from telegram_assistant.modules.drafting.pipeline import build_user_prompt, Pipeline
from telegram_assistant.events import Message
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import make_message


def test_build_user_prompt_with_all_parts():
    history = [make_message(1, "alice", "hi"), make_message(1, "me", "hello", outgoing=True)]
    out = build_user_prompt(
        enrichment="calendar: meeting at 5",
        history=history,
        instruction="ask about tomorrow",
    )
    assert "External context:" in out
    assert "calendar: meeting at 5" in out
    assert "[alice" in out
    assert "[me" in out
    assert "Extra instruction:" in out
    assert "ask about tomorrow" in out
    assert "Draft a reply." in out


def test_build_user_prompt_omits_empty_sections():
    out = build_user_prompt(
        enrichment="",
        history=[make_message(1, "alice", "hi")],
        instruction="",
    )
    assert "External context:" not in out
    assert "Extra instruction:" not in out
    assert "[alice" in out


async def test_pipeline_calls_llm_with_composed_prompt():
    llm = fake_llm("DRAFT")
    pipe = Pipeline(llm=llm, system_prompt="SP")
    out = await pipe.run(
        enrichment="E",
        history=[make_message(1, "alice", "h")],
        instruction="I",
    )
    assert out == "DRAFT"
