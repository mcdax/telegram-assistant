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
    assert "Conversation participants:" in out
    assert "Person A = alice" in out
    assert "[Person A" in out
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
    assert "Person A = alice" in out
    assert "[Person A" in out


def test_build_user_prompt_distinguishes_multiple_people():
    history = [
        make_message(1, "alice", "hi", message_id=1),
        make_message(1, "bob", "hey", message_id=2),
        make_message(1, "alice", "how are you?", message_id=3),
        make_message(1, "charlie", "hello both", message_id=4),
    ]
    out = build_user_prompt(enrichment="", history=history, instruction="")
    # Labels assigned in order of first appearance, stable across reuse.
    assert "Person A = alice" in out
    assert "Person B = bob" in out
    assert "Person C = charlie" in out
    # Alice's second message reuses the same label.
    assert out.count("[Person A") == 2
    assert out.count("[Person B") == 1
    assert out.count("[Person C") == 1


def test_build_user_prompt_omits_legend_when_only_me():
    history = [make_message(1, "me", "draft reminder", outgoing=True)]
    out = build_user_prompt(enrichment="", history=history, instruction="")
    assert "Conversation participants:" not in out
    assert "[me" in out


def test_build_user_prompt_with_empty_history():
    out = build_user_prompt(enrichment="", history=[], instruction="say hi")
    assert "Conversation participants:" not in out
    assert "Conversation so far" in out
    assert "Extra instruction:" in out
    assert "say hi" in out


async def test_pipeline_calls_llm_with_composed_prompt():
    llm = fake_llm("DRAFT")
    pipe = Pipeline(llm=llm, system_prompt="SP")
    out = await pipe.run(
        enrichment="E",
        history=[make_message(1, "alice", "h")],
        instruction="I",
    )
    assert out == "DRAFT"
