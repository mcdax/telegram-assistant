"""Agent module. Owns the /agent marker.

When the user types ``/agent <instruction>`` in any chat, the module:
  1. Debounces — each keystroke restarts a short timer (default 3s).
     Any prior in-flight run for the same chat is cancelled.
  2. Once the timer expires it clears the draft, fetches the last N
     messages of the source chat, calls the LLM, and posts the response
     to a configured Telegram bot chat.

The LLM resolution mirrors the drafting module: an optional
``[modules.agent.openai]`` block routes through ``OpenAIDrafter``;
otherwise the default Pydantic AI ``[llm]`` factory is used.

If the configured ``bot_token_env`` does not resolve to a value the
module is "send-disabled": the LLM still runs (so the user can validate
the pipeline end-to-end), but the response is logged at INFO instead of
posted to Telegram.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, Awaitable, Callable

import aiohttp

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.drafting.openai_drafter import (
    OpenAIDrafter,
    build_payload,
    load_openai_config,
)

from . import bot_sender as _bot_sender


SendText = Callable[..., Awaitable[None]]


class AgentModule:
    name = "agent"

    def __init__(self, *, send_text: SendText | None = None) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []
        self._send_text: SendText = send_text or _bot_sender.send_text
        self._bot_token: str | None = None
        self._target_chat_id: int = 0
        self._debounce_s: float = 3.0
        self._last_n: int = 20
        self._default_system_prompt: str = ""
        self._pending: dict[int, asyncio.Task[None]] = {}
        self._pending_instruction: dict[int, str] = {}
        self._openai_drafter: OpenAIDrafter | None = None

    @property
    def send_disabled(self) -> bool:
        return self._bot_token is None

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config

        target = int(cfg.get("target_chat_id", 0))
        if target == 0:
            raise ValueError(
                "agent module: target_chat_id must be a non-zero integer"
            )
        self._target_chat_id = target

        bot_token_env = cfg.get("bot_token_env") or ""
        token = os.environ.get(bot_token_env) if bot_token_env else None
        if not token:
            ctx.log.warning(
                "agent module: bot_token_env=%r not resolvable; "
                "send-disabled — LLM still runs but responses are logged "
                "at INFO instead of posted",
                bot_token_env,
            )
        self._bot_token = token

        self._debounce_s = float(cfg.get("debounce_s", 3))
        self._last_n = int(cfg.get("last_n", 20))
        self._default_system_prompt = cfg.get("default_system_prompt", "") or ""

        openai_section = cfg.get("openai") or {}
        openai_config = load_openai_config(
            openai_section,
            fallback_instruction=self._default_system_prompt,
        )
        if openai_config is not None:
            ctx.log.info(
                "agent: using OpenAI backend base_url=%s model=%s",
                openai_config.base_url, openai_config.model,
            )
            # ``openai_timeout_s`` is nested under ``[modules.agent.openai]``
            # to match the shape of ``[modules.drafting.openai]``.
            self._openai_drafter = OpenAIDrafter.from_config(
                openai_config,
                timeout_s=int(openai_section.get("openai_timeout_s", 120)),
            )

        user_markers = cfg.get("markers", {})
        self._markers = [
            Marker(
                name="agent",
                trigger=user_markers.get("agent", "/agent"),
                kind=MatchKind.CONTAINS,
                priority=60,
            ),
        ]

    async def shutdown(self) -> None:
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()
        self._pending_instruction.clear()

    def markers(self) -> list[Marker]:
        return list(self._markers)

    async def on_draft_update(
        self, event: DraftUpdate, match: MarkerMatch
    ) -> None:
        assert self._ctx is not None
        chat_id = event.chat_id
        self._pending_instruction[chat_id] = match.remainder
        self._cancel_pending(chat_id)
        self._pending[chat_id] = asyncio.create_task(self._debounced(chat_id))

    async def on_plain_draft_update(self, event: DraftUpdate) -> None:
        chat_id = event.chat_id
        if self._cancel_pending(chat_id):
            self._pending_instruction.pop(chat_id, None)

    def _cancel_pending(self, chat_id: int) -> bool:
        task = self._pending.pop(chat_id, None)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _debounced(self, chat_id: int) -> None:
        # Capture our own task identity. ``_cancel_pending`` cancels but does
        # NOT await — by the time this finally runs, ``on_draft_update`` may
        # already have stored a *replacement* task under the same chat_id.
        # Only clear the slot if it's still us.
        current = asyncio.current_task()
        try:
            await asyncio.sleep(self._debounce_s)
        except asyncio.CancelledError:
            if self._pending.get(chat_id) is current:
                self._pending.pop(chat_id, None)
            return
        try:
            await self._run(chat_id)
        finally:
            if self._pending.get(chat_id) is current:
                self._pending.pop(chat_id, None)

    async def _run(self, chat_id: int) -> None:
        assert self._ctx is not None
        instruction = self._pending_instruction.pop(chat_id, "").strip()

        # Always clear the draft first so the user's input field is empty
        # while the LLM works (which can take a long time on self-hosted
        # backends).
        await self._ctx.tg.write_draft(chat_id, "")

        if not instruction:
            self._ctx.log.debug(
                "/agent in chat=%s: empty instruction — skipping", chat_id,
            )
            return

        history = await self._ctx.tg.fetch_history(chat_id, self._last_n)
        try:
            output = await self._invoke_llm(
                chat_id=chat_id,
                history=history,
                instruction=instruction,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._ctx.log.warning("/agent llm failed chat=%s: %s", chat_id, exc)
            await self._post_error(f"❌ agent: {exc}")
            return

        if self.send_disabled:
            self._ctx.log.info(
                "/agent send-disabled — would post to chat=%s: %s",
                self._target_chat_id, output,
            )
            return

        # Narrow ``str | None`` to ``str`` for the type checker — by
        # construction ``send_disabled`` being False means ``_bot_token``
        # is set.
        assert self._bot_token is not None
        try:
            await self._send_text(
                self._ctx.http,
                self._bot_token,
                chat_id=self._target_chat_id,
                text=output,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._ctx.log.warning("/agent bot send failed: %s", exc)

    async def _invoke_llm(
        self,
        *,
        chat_id: int,
        history: list,
        instruction: str,
    ) -> str:
        assert self._ctx is not None
        if self._openai_drafter is not None:
            return await self._openai_drafter.draft(
                chat_id=chat_id,
                chat_title="",
                history=history,
                instruction=instruction,
            )
        payload = build_payload(
            chat_id=chat_id,
            chat_title="",
            history=history,
            instruction=instruction,
        )
        json_content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if self._default_system_prompt.strip():
            user_text = f"{self._default_system_prompt.strip()}\n\n{json_content}"
        else:
            user_text = json_content
        agent = self._ctx.llm.agent("")
        return await self._ctx.llm.run(agent, user_text)

    async def _post_error(self, text: str) -> None:
        if self.send_disabled:
            return
        assert self._ctx is not None
        assert self._bot_token is not None
        try:
            await self._send_text(
                self._ctx.http,
                self._bot_token,
                chat_id=self._target_chat_id,
                text=text,
            )
        except Exception as exc:
            self._ctx.log.warning("/agent error post failed: %s", exc)
