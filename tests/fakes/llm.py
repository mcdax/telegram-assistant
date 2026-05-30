"""Test helpers for building LLM factories that return canned responses."""
from __future__ import annotations

from pydantic_ai.models.test import TestModel

from telegram_assistant.llm import LLMFactory


def fake_llm(response: str, *, timeout_s: int = 5) -> LLMFactory:
    return LLMFactory(model=TestModel(custom_output_text=response), timeout_s=timeout_s)


class RecordingLLM(LLMFactory):
    """LLMFactory that records the user_text of every run() call."""

    def __init__(self, response: str, *, timeout_s: int = 5) -> None:
        super().__init__(model=TestModel(custom_output_text=response), timeout_s=timeout_s)
        self.calls: list[str] = []

    async def run(self, agent, user_text: str) -> str:  # type: ignore[override]
        self.calls.append(user_text)
        return await super().run(agent, user_text)
