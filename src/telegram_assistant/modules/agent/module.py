"""Agent module. Owns the /agent marker.

When the user types ``/agent <instruction>`` in any chat, the module:
  1. Debounces — each keystroke restarts a short timer (default 3s).
     Any prior in-flight run for the same chat is cancelled.
  2. Once the timer expires it clears the draft, fetches the last N
     messages of the source chat, calls the LLM, and posts the response
     to a configured Telegram bot chat.

If the LLM response starts with ``CLARIFY:`` the module posts the
question to the bot chat and waits for the user to reply there (either
by replying to the question message or — when only one session is
active — by sending a plain message).  The user's answer is fed back
to the LLM together with the accumulated conversation, and the loop
repeats until the LLM responds with ``ANSWER:`` or the
``max_clarify_rounds`` / ``clarify_timeout_s`` limit is hit.

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
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import aiohttp

from telegram_assistant.events import DraftUpdate, OutgoingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.drafting.openai_drafter import (
    OpenAIDrafter,
    build_payload,
    load_openai_config,
)

from . import bot_sender as _bot_sender

SendText = Callable[..., Awaitable[int]]  # returns message_id

_CLARIFY_PREFIX = "CLARIFY:"
_ANSWER_PREFIX = "ANSWER:"

_CLARIFY_SYSTEM_SUFFIX = (
    "\n\nIf the instruction is ambiguous or missing details, start your "
    'response with "CLARIFY:" followed by a concise question. '
    'Otherwise start with "ANSWER:" followed by the result. '
    "After the user answers a clarification, make a best-effort attempt "
    "using the information provided. Infer sensible defaults for optional "
    "details (for example, a calendar title or default duration) instead "
    "of asking again. Ask another CLARIFY question only when a missing "
    "detail is strictly required to perform the action."
)


@dataclass
class AgentSession:
    """State for a single multi-turn agent clarification session."""

    source_chat_id: int
    history: list
    instruction: str
    conversation: list[str] = field(default_factory=list)
    rounds: int = 0
    future: asyncio.Future[str] | None = None
    clarify_msg_id: int | None = None


class AgentModule:
    name = "agent"

    def __init__(self, *, send_text: SendText | None = None) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []
        self._send_text: SendText = send_text or _bot_sender.send_text
        self._bot_token: str | None = None
        self._target_chat_id: int = 0
        self._bot_chat_id: int | None = None
        self._debounce_s: float = 3.0
        self._last_n: int = 20
        self._default_system_prompt: str = ""
        self._pending: dict[int, asyncio.Task[None]] = {}
        self._pending_instruction: dict[int, str] = {}
        self._openai_drafter: OpenAIDrafter | None = None
        # Clarification state
        self._active: dict[int, AgentSession] = {}  # key = target_chat_id
        self._clarify_timeout_s: float = 300.0
        self._max_clarify_rounds: int = 3

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
        # In a private Telegram conversation the bot's user ID is the
        # ``chat_id`` seen by Telethon for messages sent by the user in the
        # bot chat.  It is the numeric prefix of a Bot API token.  This is
        # deliberately separate from target_chat_id: the latter is the
        # recipient chat for Bot API sends (Philipp's user ID), while this
        # one identifies the chat in which the user replies.
        if token:
            try:
                self._bot_chat_id = int(token.split(":", 1)[0])
            except (TypeError, ValueError):
                ctx.log.warning(
                    "agent module: could not derive bot chat id from token; "
                    "falling back to target_chat_id for reply matching",
                )
        if self._bot_chat_id is None:
            self._bot_chat_id = target

        self._debounce_s = float(cfg.get("debounce_s", 3))
        self._last_n = int(cfg.get("last_n", 20))
        self._default_system_prompt = cfg.get("default_system_prompt", "") or ""
        self._clarify_timeout_s = float(cfg.get("clarify_timeout_s", 300))
        self._max_clarify_rounds = int(cfg.get("max_clarify_rounds", 3))

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
        for session in self._active.values():
            if session.future and not session.future.done():
                session.future.cancel()
        self._active.clear()

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

    async def on_outgoing_message(self, event: OutgoingMessage) -> None:
        """Handle replies in the bot chat for active clarification sessions."""
        msg = event.message
        if msg.chat_id != self._bot_chat_id:
            return

        # Option B: reply-to matching — check if the message is a reply
        # to a known clarify question.
        if msg.reply_to_message_id is not None:
            for key, session in self._active.items():
                fut = session.future
                if (
                    session.clarify_msg_id == msg.reply_to_message_id
                    and fut is not None
                    and not fut.done()
                ):
                    fut.set_result(msg.text)
                    return

        # Option A fallback: if exactly one session is active, resolve it.
        active = [s for s in self._active.values() if s.future is not None and not s.future.done()]
        if len(active) == 1:
            fut = active[0].future
            assert fut is not None
            fut.set_result(msg.text)

    def _cancel_pending(self, chat_id: int) -> bool:
        task = self._pending.pop(chat_id, None)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _debounced(self, chat_id: int) -> None:
        current = asyncio.current_task()
        try:
            await asyncio.sleep(self._debounce_s)
        except asyncio.CancelledError:
            if self._pending.get(chat_id) is current:
                self._pending.pop(chat_id, None)
            return
        try:
            await self._run_session(chat_id)
        finally:
            if self._pending.get(chat_id) is current:
                self._pending.pop(chat_id, None)

    async def _run_session(self, chat_id: int) -> None:
        assert self._ctx is not None
        instruction = self._pending_instruction.pop(chat_id, "").strip()

        await self._ctx.tg.write_draft(chat_id, "")

        if not instruction:
            self._ctx.log.debug(
                "/agent in chat=%s: empty instruction — skipping", chat_id,
            )
            return

        # Cancel any active clarification session (Option A: serial).
        self._cancel_active_session()

        history = await self._ctx.tg.fetch_history(chat_id, self._last_n)

        # First LLM call
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
            await self._post(f"❌ agent: {exc}")
            return

        # CLARIFY / ANSWER loop
        session = AgentSession(
            source_chat_id=chat_id,
            history=history,
            instruction=instruction,
        )

        while output.startswith(_CLARIFY_PREFIX):
            session.rounds += 1
            if session.rounds > self._max_clarify_rounds:
                await self._post("⚠️ Max clarification rounds reached — please rephrase.")
                self._cancel_active_session()
                return

            question = output[len(_CLARIFY_PREFIX):].strip()
            msg_id = await self._post(f"❓ {question}")
            session.clarify_msg_id = msg_id
            session.future = asyncio.get_event_loop().create_future()

            # Register as the active session so on_outgoing_message can
            # resolve it (Option A: single active session).
            self._active[self._target_chat_id] = session

            try:
                user_reply = await asyncio.wait_for(
                    session.future, timeout=self._clarify_timeout_s
                )
            except asyncio.TimeoutError:
                await self._post("⏰ Clarification timeout — session cancelled.")
                self._active.pop(self._target_chat_id, None)
                return
            except asyncio.CancelledError:
                self._active.pop(self._target_chat_id, None)
                raise

            self._active.pop(self._target_chat_id, None)

            # Feed the user's reply back to the LLM
            session.conversation.append(f"CLARIFY: {question}")
            session.conversation.append(f"USER: {user_reply}")

            combined = self._build_multi_turn_instruction(
                instruction, session.conversation
            )
            try:
                output = await self._invoke_llm(
                    chat_id=chat_id,
                    history=history,
                    instruction=combined,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._ctx.log.warning(
                    "/agent llm failed during clarification chat=%s: %s",
                    chat_id, exc,
                )
                await self._post(f"❌ agent: {exc}")
                return

        # Final answer
        answer = (
            output[len(_ANSWER_PREFIX):].strip()
            if output.startswith(_ANSWER_PREFIX)
            else output
        )
        await self._post(answer)
        self._cancel_active_session()

    def _cancel_active_session(self) -> None:
        """Cancel and clear any active clarification session."""
        session = self._active.pop(self._target_chat_id, None)
        if session and session.future and not session.future.done():
            session.future.cancel()

    def _build_multi_turn_instruction(
        self, original: str, conversation: list[str]
    ) -> str:
        """Build the instruction text for follow-up LLM calls."""
        parts = [f"Original instruction: {original}", "Conversation so far:"]
        parts.extend(conversation)
        parts.append("Now respond with ANSWER: or CLARIFY:")
        return "\n".join(parts)

    async def _post(self, text: str) -> int | None:
        """Post a message to the bot chat. Returns message_id or None."""
        assert self._ctx is not None
        if self.send_disabled:
            self._ctx.log.info(
                "/agent send-disabled — would post to chat=%s: %s",
                self._target_chat_id, text,
            )
            return None
        assert self._bot_token is not None
        try:
            return await self._send_text(
                self._ctx.http,
                self._bot_token,
                chat_id=self._target_chat_id,
                text=text,
            )
        except Exception as exc:
            self._ctx.log.warning("/agent bot send failed: %s", exc)
            return None

    async def _invoke_llm(
        self,
        *,
        chat_id: int,
        history: list,
        instruction: str,
    ) -> str:
        assert self._ctx is not None
        # Inject the clarify protocol into the system prompt
        full_instruction = instruction + _CLARIFY_SYSTEM_SUFFIX

        if self._openai_drafter is not None:
            return await self._openai_drafter.draft(
                chat_id=chat_id,
                chat_title="",
                history=history,
                instruction=full_instruction,
            )
        payload = build_payload(
            chat_id=chat_id,
            chat_title="",
            history=history,
            instruction=full_instruction,
        )
        json_content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if self._default_system_prompt.strip():
            user_text = f"{self._default_system_prompt.strip()}\n\n{json_content}"
        else:
            user_text = json_content
        agent = self._ctx.llm.agent("")
        return await self._ctx.llm.run(agent, user_text)