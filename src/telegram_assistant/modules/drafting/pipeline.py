"""Draft-generation orchestration. Pure logic, no Telegram I/O."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from telegram_assistant.events import Message
from telegram_assistant.llm import LLMFactory


def build_user_prompt(enrichment: str, history: Sequence[Message], instruction: str) -> str:
    parts: list[str] = []
    if enrichment.strip():
        parts.append(f"External context:\n{enrichment.strip()}\n")
    parts.append("Conversation so far (most recent last):")
    for m in history:
        who = "me" if m.outgoing else m.sender
        parts.append(f"[{who} {m.timestamp.isoformat()}] {m.text}")
    if instruction.strip():
        parts.append(f"\nExtra instruction:\n{instruction.strip()}\n")
    parts.append("\nDraft a reply.")
    return "\n".join(parts)


@dataclass
class Pipeline:
    llm: LLMFactory
    system_prompt: str

    async def run(
        self, *, enrichment: str, history: Sequence[Message], instruction: str
    ) -> str:
        agent = self.llm.agent(self.system_prompt)
        prompt = build_user_prompt(enrichment, history, instruction)
        return await self.llm.run(agent, prompt)
