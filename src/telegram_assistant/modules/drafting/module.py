"""Drafting module. Owns /draft, /auto on, /auto off markers and auto-draft policy."""
from __future__ import annotations

from typing import Any

from telegram_assistant.events import DraftUpdate, IncomingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.state import StateWriteError

from .enricher import Enricher
from .pipeline import Pipeline


DEFAULT_MARKERS = {"draft": "/draft", "auto_on": "/auto on", "auto_off": "/auto off"}


class DraftingModule:
    name = "drafting"

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []
        self._auto_draft_seed: set[int] = set()
        self._per_chat: dict[str, dict[str, Any]] = {}

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        user_markers = cfg.get("markers", {})
        self._markers = [
            Marker(
                name="auto_on",
                trigger=user_markers.get("auto_on", DEFAULT_MARKERS["auto_on"]),
                kind=MatchKind.EXACT,
                priority=100,
            ),
            Marker(
                name="auto_off",
                trigger=user_markers.get("auto_off", DEFAULT_MARKERS["auto_off"]),
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
        self._enricher = Enricher(
            http=ctx.http,
            url=cfg.get("enrichment_url", ""),
            auth_header=cfg.get("enrichment_auth_header") or None,
            timeout_s=int(cfg.get("enrichment_timeout_s", 10)),
        )

    async def shutdown(self) -> None:
        return

    def markers(self) -> list[Marker]:
        return list(self._markers)

    async def on_incoming_message(self, event: IncomingMessage) -> None:
        assert self._ctx is not None
        msg = event.message
        if msg.outgoing:
            return
        if not self._auto_on(msg.chat_id):
            return
        await self._draft(chat_id=msg.chat_id, chat_title=msg.sender, instruction="")

    async def on_draft_update(self, event: DraftUpdate, match: MarkerMatch) -> None:
        assert self._ctx is not None
        name = match.marker.name
        if name == "auto_on":
            await self._set_auto(event.chat_id, True)
        elif name == "auto_off":
            await self._set_auto(event.chat_id, False)
        elif name == "draft":
            await self._draft(chat_id=event.chat_id, chat_title="", instruction=match.remainder)

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
        history = await self._ctx.tg.fetch_history(chat_id, last_n)
        enrichment = await self._enricher.fetch(chat_id, chat_title, history)
        pipeline = Pipeline(llm=self._ctx.llm, system_prompt=system_prompt)
        try:
            output = await pipeline.run(
                enrichment=enrichment, history=history, instruction=instruction
            )
        except Exception as e:
            self._ctx.log.warning("drafting failed: %s", e)
            return
        await self._ctx.tg.write_draft(chat_id, output)
