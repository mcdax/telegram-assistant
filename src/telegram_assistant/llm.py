"""LLM factory. Thin wrapper over pydantic-ai."""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import Agent


class LLMTimeout(TimeoutError):
    pass


class LLMFactory:
    def __init__(self, model: Any, timeout_s: int) -> None:
        self._model = model
        self._timeout_s = timeout_s

    def agent(self, system_prompt: str) -> Agent[None, str]:
        return Agent(self._model, system_prompt=system_prompt, output_type=str)

    async def run(self, agent: Agent[None, str], user_text: str) -> str:
        try:
            result = await asyncio.wait_for(agent.run(user_text), timeout=self._timeout_s)
        except asyncio.TimeoutError as e:
            raise LLMTimeout(f"LLM call exceeded {self._timeout_s}s") from e
        return str(result.output)
