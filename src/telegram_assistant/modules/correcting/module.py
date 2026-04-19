"""Correcting module. Owns the /fix marker for grammar/spelling/punctuation rewrites."""
from __future__ import annotations

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext


DEFAULT_TRIGGER = "/fix"


class CorrectingModule:
    name = "correcting"

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._marker: Marker | None = None

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        trigger = ctx.config.get("markers", {}).get("fix", DEFAULT_TRIGGER)
        self._marker = Marker(
            name="fix", trigger=trigger, kind=MatchKind.CONTAINS, priority=70
        )

    async def shutdown(self) -> None:
        return

    def markers(self) -> list[Marker]:
        assert self._marker is not None
        return [self._marker]

    async def on_draft_update(self, event: DraftUpdate, match: MarkerMatch) -> None:
        assert self._ctx is not None
        text = match.remainder.strip()
        if not text:
            self._ctx.log.info("/fix with empty remainder — ignored")
            return
        agent = self._ctx.llm.agent(self._ctx.config["system_prompt"])
        try:
            output = await self._ctx.llm.run(agent, text)
        except Exception as e:
            self._ctx.log.warning("correcting failed: %s", e)
            return
        await self._ctx.tg.write_draft(event.chat_id, output)
