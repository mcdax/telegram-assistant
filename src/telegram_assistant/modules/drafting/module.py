"""Drafting module. Owns /draft, /auto_draft on, /auto_draft off markers and auto-draft policy."""
from __future__ import annotations

from typing import Any

from telegram_assistant.events import DraftUpdate, IncomingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.state import StateWriteError

from .enricher import Enricher
from .pipeline import Pipeline


DEFAULT_MARKERS = {
    "draft": "/draft",
    "auto_draft_on": "/auto_draft on",
    "auto_draft_off": "/auto_draft off",
}


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
            self._ctx.log.debug("skip incoming chat=%s: outgoing", msg.chat_id)
            return
        if not self._auto_on(msg.chat_id):
            self._ctx.log.debug("skip incoming chat=%s: auto-draft off", msg.chat_id)
            return
        self._ctx.log.debug("auto-drafting for chat=%s", msg.chat_id)
        await self._draft(chat_id=msg.chat_id, chat_title=msg.sender, instruction="")

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
        enrichment = await self._enricher.fetch(chat_id, chat_title, history)
        self._ctx.log.debug("enrichment chat=%s len=%d", chat_id, len(enrichment))
        pipeline = Pipeline(llm=self._ctx.llm, system_prompt=system_prompt)
        try:
            output = await pipeline.run(
                enrichment=enrichment, history=history, instruction=instruction
            )
        except Exception as e:
            self._ctx.log.warning("drafting failed chat=%s: %s", chat_id, e)
            return
        self._ctx.log.debug("draft generated chat=%s len=%d", chat_id, len(output))
        await self._ctx.tg.write_draft(chat_id, output)
