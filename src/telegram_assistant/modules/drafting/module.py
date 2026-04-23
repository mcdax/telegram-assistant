"""Drafting module. Owns /draft, /auto_draft on, /auto_draft off markers and auto-draft policy.

Auto-draft debouncing:
  * First inbound activity in an idle chat drafts immediately.
  * While "in cooldown" (< ``auto_draft_debounce_s`` seconds since the last
    generated draft), new inbound activity (incoming messages, edits to
    incoming messages) schedules a delayed draft. Each new activity
    re-starts the timer, so we only redraft after a period of silence.
  * A message the user sends clears the cooldown entirely: the draft is
    assumed to have been consumed / made moot.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from telegram_assistant.events import (
    DraftUpdate,
    IncomingMessage,
    MessageEdited,
    OutgoingMessage,
)
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.state import StateWriteError

from .pipeline import Pipeline


DEFAULT_MARKERS = {
    "draft": "/draft",
    "auto_draft_on": "/auto_draft on",
    "auto_draft_off": "/auto_draft off",
}
DEFAULT_DEBOUNCE_S = 60


class DraftingModule:
    name = "drafting"

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []
        self._auto_draft_seed: set[int] = set()
        self._per_chat: dict[str, dict[str, Any]] = {}
        self._debounce_s: int = DEFAULT_DEBOUNCE_S
        self._last_drafted_at: dict[int, float] = {}
        self._pending: dict[int, asyncio.Task[None]] = {}
        # True while the user's official client has a non-empty draft synced
        # for this chat; used to suppress auto-draft writes that would
        # overwrite the user's in-progress typing.
        self._user_drafting: dict[int, bool] = {}

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        user_markers = cfg.get("markers", {})
        self._markers = [
            Marker(
                name="auto_draft_on",
                trigger=user_markers.get("auto_draft_on", DEFAULT_MARKERS["auto_draft_on"]),
                kind=MatchKind.EXACT,
                priority=100,
            ),
            Marker(
                name="auto_draft_off",
                trigger=user_markers.get("auto_draft_off", DEFAULT_MARKERS["auto_draft_off"]),
                kind=MatchKind.EXACT,
                priority=100,
            ),
            Marker(
                name="draft",
                trigger=user_markers.get("draft", DEFAULT_MARKERS["draft"]),
                kind=MatchKind.CONTAINS,
                priority=50,
            ),
        ]
        self._auto_draft_seed = {int(c) for c in cfg.get("auto_draft_chats", [])}
        self._per_chat = cfg.get("chats", {})
        self._debounce_s = int(cfg.get("auto_draft_debounce_s", DEFAULT_DEBOUNCE_S))

    async def shutdown(self) -> None:
        for task in list(self._pending.values()):
            task.cancel()
        self._pending.clear()

    def markers(self) -> list[Marker]:
        return list(self._markers)

    async def on_incoming_message(self, event: IncomingMessage) -> None:
        assert self._ctx is not None
        msg = event.message
        if msg.outgoing:
            self._ctx.log.debug("skip incoming chat=%s: outgoing", msg.chat_id)
            return
        if not self._auto_on(msg.chat_id):
            self._ctx.log.debug("skip incoming chat=%s: auto-draft off", msg.chat_id)
            return
        await self._trigger_auto_draft(msg.chat_id, chat_title=msg.sender, trigger="incoming")

    async def on_message_edited(self, event: MessageEdited) -> None:
        assert self._ctx is not None
        msg = event.message
        if msg.outgoing:
            return
        if not self._auto_on(msg.chat_id):
            self._ctx.log.debug("skip edit chat=%s: auto-draft off", msg.chat_id)
            return
        await self._trigger_auto_draft(msg.chat_id, chat_title=msg.sender, trigger="edit")

    async def on_outgoing_message(self, event: OutgoingMessage) -> None:
        """Our pending debounce is moot once the user sends: start fresh next time."""
        assert self._ctx is not None
        chat_id = event.message.chat_id
        if self._cancel_pending(chat_id):
            self._ctx.log.debug("outgoing in chat=%s cancelled pending debounce", chat_id)
        if chat_id in self._last_drafted_at:
            self._last_drafted_at.pop(chat_id, None)
            self._ctx.log.debug("outgoing in chat=%s cleared cooldown", chat_id)
        # User sent → any prior in-progress draft is consumed.
        self._user_drafting.pop(chat_id, None)

    async def on_plain_draft_update(self, event: DraftUpdate) -> None:
        """Track whether the user has a non-empty draft in this chat.

        Also cancel any pending auto-draft task: the user has started
        writing their own reply, so overwriting their input field would
        destroy their work.
        """
        assert self._ctx is not None
        chat_id = event.chat_id
        if event.text.strip():
            was = self._user_drafting.get(chat_id, False)
            self._user_drafting[chat_id] = True
            if not was and self._cancel_pending(chat_id):
                self._ctx.log.debug(
                    "user started drafting in chat=%s — cancelled pending auto-draft",
                    chat_id,
                )
        else:
            # Empty draft — user cleared their input.
            if self._user_drafting.pop(chat_id, False):
                self._ctx.log.debug("user cleared draft in chat=%s", chat_id)

    async def on_draft_update(self, event: DraftUpdate, match: MarkerMatch) -> None:
        assert self._ctx is not None
        name = match.marker.name
        self._ctx.log.debug(
            "on_draft_update chat=%s marker=%s remainder=%r",
            event.chat_id, name, match.remainder[:80],
        )
        if name == "auto_draft_on":
            await self._set_auto(event.chat_id, True)
        elif name == "auto_draft_off":
            await self._set_auto(event.chat_id, False)
        elif name == "draft":
            await self._draft(chat_id=event.chat_id, chat_title="", instruction=match.remainder)

    async def _trigger_auto_draft(
        self, chat_id: int, *, chat_title: str, trigger: str
    ) -> None:
        """Decide: draft now (idle), or schedule a debounced draft (cooldown)."""
        assert self._ctx is not None
        if self._user_drafting.get(chat_id, False):
            self._ctx.log.debug(
                "auto-draft chat=%s trigger=%s skipped: user is drafting",
                chat_id, trigger,
            )
            return
        self._cancel_pending(chat_id)

        last = self._last_drafted_at.get(chat_id)
        now = time.monotonic()
        in_cooldown = (
            self._debounce_s > 0 and last is not None and (now - last) < self._debounce_s
        )
        if not in_cooldown:
            self._ctx.log.debug(
                "auto-drafting chat=%s trigger=%s (idle or debounce disabled)",
                chat_id, trigger,
            )
            await self._draft(chat_id=chat_id, chat_title=chat_title, instruction="")
            self._last_drafted_at[chat_id] = time.monotonic()
        else:
            self._ctx.log.debug(
                "auto-drafting chat=%s trigger=%s deferred %ds (cooldown active)",
                chat_id, trigger, self._debounce_s,
            )
            self._pending[chat_id] = asyncio.create_task(
                self._debounced(chat_id, chat_title)
            )

    async def _debounced(self, chat_id: int, chat_title: str) -> None:
        try:
            await asyncio.sleep(self._debounce_s)
        except asyncio.CancelledError:
            return
        # Final guard: user started typing during our wait — abort.
        if self._user_drafting.get(chat_id, False):
            assert self._ctx is not None
            self._ctx.log.debug(
                "debounced auto-draft chat=%s aborted: user is drafting", chat_id,
            )
            self._pending.pop(chat_id, None)
            return
        try:
            await self._draft(chat_id=chat_id, chat_title=chat_title, instruction="")
        finally:
            self._last_drafted_at[chat_id] = time.monotonic()
            self._pending.pop(chat_id, None)

    def _cancel_pending(self, chat_id: int) -> bool:
        task = self._pending.pop(chat_id, None)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def _set_auto(self, chat_id: int, on: bool) -> None:
        assert self._ctx is not None
        try:
            self._ctx.state.set("auto_draft", str(chat_id), on)
            word = "enabled" if on else "disabled"
            text = f"✓ Auto-draft {word} for this chat"
        except StateWriteError:
            text = "✗ state write failed"
        await self._ctx.tg.write_draft(chat_id, text)

    def _auto_on(self, chat_id: int) -> bool:
        assert self._ctx is not None
        override = self._ctx.state.get("auto_draft", str(chat_id), default=None)
        if override is not None:
            return bool(override)
        return chat_id in self._auto_draft_seed

    def _resolve_for_chat(self, chat_id: int) -> tuple[str, int]:
        per = self._per_chat.get(str(chat_id), {})
        system_prompt = per.get("system_prompt", self._ctx.config["default_system_prompt"])
        last_n = int(per.get("last_n", self._ctx.config["last_n"]))
        return system_prompt, last_n

    async def _draft(self, *, chat_id: int, chat_title: str, instruction: str) -> None:
        assert self._ctx is not None
        system_prompt, last_n = self._resolve_for_chat(chat_id)
        self._ctx.log.debug(
            "draft chat=%s last_n=%d instruction=%r system_prompt_len=%d",
            chat_id, last_n, instruction[:80], len(system_prompt),
        )
        history = await self._ctx.tg.fetch_history(chat_id, last_n)
        self._ctx.log.debug("fetched history chat=%s messages=%d", chat_id, len(history))
        pipeline = Pipeline(llm=self._ctx.llm, system_prompt=system_prompt)
        try:
            output = await pipeline.run(
                enrichment="", history=history, instruction=instruction
            )
        except Exception as e:
            self._ctx.log.warning("drafting failed chat=%s: %s", chat_id, e)
            return
        self._ctx.log.debug("draft generated chat=%s len=%d", chat_id, len(output))
        await self._ctx.tg.write_draft(chat_id, output)
