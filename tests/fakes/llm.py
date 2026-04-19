"""Test helpers for building LLM factories that return canned responses."""
from __future__ import annotations

from pydantic_ai.models.test import TestModel

from telegram_assistant.llm import LLMFactory


def fake_llm(response: str, *, timeout_s: int = 5) -> LLMFactory:
    return LLMFactory(model=TestModel(custom_output_text=response), timeout_s=timeout_s)
