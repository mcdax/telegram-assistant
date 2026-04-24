"""Per-module OpenAI-compatible drafting backend.

Uses Pydantic AI's ``OpenAIProvider`` + ``OpenAIChatModel`` under the hood
— the same abstraction the correcting module uses — just configured with
a caller-provided ``base_url``, ``api_key``, ``model``, and
``instruction``. The value-add vs. the default drafting path is the
**structured JSON payload**: chat_id + per-message sender_id / is_me /
type / attachment descriptor, so the model sees every signal it could
need to draft a response.

The ``instruction`` text is prepended to the user message (ahead of the
JSON payload) rather than sent as a ``system`` role message, so the
whole prompt is visible in one place when browsing provider logs and
there is no reliance on the endpoint honouring system-role semantics.

Config lives under ``[modules.drafting.openai]`` — see
``load_openai_config`` for the validation rules.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Sequence

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from telegram_assistant.events import Attachment, Message
from telegram_assistant.llm import LLMFactory


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenAIConfig:
    base_url: str
    api_key: str
    model: str
    instruction: str


def load_openai_config(
    section: dict[str, Any] | None, fallback_instruction: str
) -> OpenAIConfig | None:
    """Return a validated ``OpenAIConfig``, or None if the section is incomplete.

    Rules:
      * ``section`` must carry ``base_url``, ``model``, and ``api_key_env``.
        Any missing → ``None`` (caller falls back to the default backend).
      * ``api_key_env`` names the env var that holds the actual key. The
        key itself is never written to ``config.toml``.
      * ``instruction`` is optional; falls back to the caller-supplied
        ``fallback_instruction`` when absent.
    """
    if not section:
        return None
    base_url = section.get("base_url")
    model = section.get("model")
    api_key_env = section.get("api_key_env")
    if not base_url or not model or not api_key_env:
        return None
    api_key = os.environ.get(api_key_env)
    if not api_key:
        log.warning(
            "drafting.openai.api_key_env=%s is not set in environment; "
            "falling back to the default drafting backend",
            api_key_env,
        )
        return None
    instruction = section.get("instruction") or fallback_instruction
    return OpenAIConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        instruction=instruction,
    )


def _attachment_to_dict(att: Attachment | None) -> dict[str, Any] | None:
    if att is None:
        return None
    return {"type": att.type, "description": att.description, "url": att.url}


def build_payload(
    *,
    chat_id: int,
    chat_title: str,
    history: Sequence[Message],
    instruction: str,
) -> dict[str, Any]:
    """Compose the structured JSON payload sent to the LLM.

    Shape is deliberately verbose so the model sees every signal:
    per-message is_me, sender_id, chat_id, message type, and a structured
    attachment descriptor (type, description, url).
    """
    return {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "messages": [
            {
                "time": m.timestamp.isoformat(),
                "chat_id": m.chat_id,
                "sender": m.sender,
                "sender_id": m.sender_id,
                "is_me": m.outgoing,
                "type": m.message_type,
                "text": m.text,
                "attachment": _attachment_to_dict(m.attachment),
            }
            for m in history
        ],
        "instruction": instruction or None,
    }


class OpenAIDrafter:
    """Light wrapper: feeds ``build_payload`` JSON through an ``LLMFactory``.

    The factory can be an ``OpenAIChatModel`` + ``OpenAIProvider`` pair
    built from an ``OpenAIConfig`` (see ``from_config``) — or any Pydantic
    AI model wrapped in an ``LLMFactory``, which makes the class trivial
    to test with ``TestModel``.
    """

    def __init__(self, *, factory: LLMFactory, instruction: str) -> None:
        self._factory = factory
        self._instruction = instruction

    @classmethod
    def from_config(cls, config: OpenAIConfig, *, timeout_s: int) -> "OpenAIDrafter":
        provider = OpenAIProvider(base_url=config.base_url, api_key=config.api_key)
        model = OpenAIChatModel(model_name=config.model, provider=provider)
        factory = LLMFactory(model=model, timeout_s=timeout_s)
        return cls(factory=factory, instruction=config.instruction)

    @property
    def instruction(self) -> str:
        return self._instruction

    async def draft(
        self,
        *,
        chat_id: int,
        chat_title: str,
        history: Sequence[Message],
        instruction: str,
    ) -> str:
        payload = build_payload(
            chat_id=chat_id,
            chat_title=chat_title,
            history=history,
            instruction=instruction,
        )
        json_content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        # Prepend the configured instruction to the user message rather than
        # sending it as a separate system-role message. Keeps the whole prompt
        # visible in one place and avoids surprises with endpoints that treat
        # the system role differently.
        if self._instruction.strip():
            user_content = f"{self._instruction.strip()}\n\n{json_content}"
        else:
            user_content = json_content
        log.debug(
            "openai drafter request payload_len=%d history=%d instruction_len=%d",
            len(user_content), len(payload["messages"]), len(self._instruction),
        )
        agent = self._factory.agent("")  # no system message
        output = await self._factory.run(agent, user_content)
        log.debug("openai drafter response len=%d", len(output))
        return output
