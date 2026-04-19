"""Draft-generation orchestration. Pure logic, no Telegram I/O."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from telegram_assistant.events import Message
from telegram_assistant.llm import LLMFactory


def _person_label(n: int) -> str:
    """Stable labels Person A, Person B, ..., Person Z, Person AA, ..."""
    letters = ""
    x = n
    while True:
        letters = chr(ord("A") + (x % 26)) + letters
        x = x // 26 - 1
        if x < 0:
            break
    return f"Person {letters}"


def build_user_prompt(enrichment: str, history: Sequence[Message], instruction: str) -> str:
    parts: list[str] = []
    if enrichment.strip():
        parts.append(f"External context:\n{enrichment.strip()}\n")

    # Anonymise non-self senders with Person A, B, C... in order of first appearance
    # so the LLM can tell speakers apart in group chats without leaking usernames
    # into whatever output style the user has configured.
    person_by_sender: dict[str, str] = {}
    for m in history:
        if m.outgoing:
            continue
        if m.sender not in person_by_sender:
            person_by_sender[m.sender] = _person_label(len(person_by_sender))

    if person_by_sender:
        parts.append("Conversation participants:")
        parts.append("  me = you (the user drafting this reply)")
        for sender, label in person_by_sender.items():
            parts.append(f"  {label} = {sender}")
        parts.append("")

    parts.append("Conversation so far (most recent last):")
    for m in history:
        who = "me" if m.outgoing else person_by_sender[m.sender]
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
