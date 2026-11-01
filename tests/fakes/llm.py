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


class ScriptedLLM(LLMFactory):
    """LLMFactory returning ``responses`` in order (the last one repeats);
    records every user_text (``calls``) and system prompt (``system_prompts``).
    An Exception instance in the list is raised."""

    def __init__(self, *responses: str | Exception, timeout_s: int = 5) -> None:
        super().__init__(model=TestModel(), timeout_s=timeout_s)
        self._responses = list(responses)
        self.calls: list[str] = []
        self.system_prompts: list[str] = []

    def agent(self, system_prompt: str):  # type: ignore[override]
        self.system_prompts.append(system_prompt)
        return super().agent(system_prompt)

    async def run(self, agent, user_text: str) -> str:  # type: ignore[override]
        self.calls.append(user_text)
        idx = min(len(self.calls), len(self._responses)) - 1
        response = self._responses[idx]
        if isinstance(response, Exception):
            raise response
        return response
