from __future__ import annotations

import pytest

from tests.fakes.llm import fake_llm


async def test_agent_runs_and_returns_text():
    llm = fake_llm("generated reply")
    agent = llm.agent("you are a helper")
    out = await llm.run(agent, "hello")
    assert out == "generated reply"


async def test_agent_timeout_raises():
    import asyncio

    from pydantic_ai.models.test import TestModel

    from telegram_assistant.llm import LLMFactory, LLMTimeout

    class SlowModel(TestModel):
        async def request(self, *a, **kw):
            await asyncio.sleep(5)
            return await super().request(*a, **kw)

    llm = LLMFactory(model=SlowModel(custom_output_text="result"), timeout_s=0)
    agent = llm.agent("sys")
    with pytest.raises(LLMTimeout):
        await llm.run(agent, "u")
