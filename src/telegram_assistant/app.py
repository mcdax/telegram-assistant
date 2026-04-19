"""Application glue.

Routing:
- DraftUpdate → marker_registry resolves winning module → on_draft_update.
- IncomingMessage → broadcast to all modules implementing on_incoming_message.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp

from .event_bus import EventBus
from .events import DraftUpdate, IncomingMessage
from .llm import LLMFactory
from .loop_protection import LoopProtection
from .markers import MarkerRegistry
from .module import Module, ModuleContext
from .module_loader import ModuleLoader
from .state import RuntimeState
from .telegram_client import TelegramClient

log = logging.getLogger(__name__)


class App:
    def __init__(
        self,
        *,
        tg: TelegramClient,
        llm: LLMFactory,
        http: aiohttp.ClientSession,
        state_path: Path,
    ) -> None:
        self._tg = tg
        self._llm = llm
        self._http = http
        self._state = RuntimeState(state_path)
        self._bus = EventBus()
        self._registry = MarkerRegistry()
        self._loop_protect = LoopProtection()
        self._modules: list[Module] = []

    async def start(self, modules_cfg: dict[str, dict[str, Any]]) -> None:
        def make_ctx(module_name: str, config: dict[str, Any]) -> ModuleContext:
            return ModuleContext(
                tg=_LoopProtectingClient(self._tg, self._loop_protect),
                llm=self._llm,
                http=self._http,
                config=config,
                state=self._state.for_module(module_name),
                log=logging.getLogger(f"module.{module_name}"),
            )

        self._modules = await ModuleLoader().load(modules_cfg, self._registry, make_ctx)

        for m in self._modules:
            if hasattr(m, "on_incoming_message"):
                self._bus.subscribe("incoming", m.name, m.on_incoming_message)
            if hasattr(m, "on_draft_update"):
                async def _draft_handler(payload, _m=m):
                    ev, mt = payload
                    await _m.on_draft_update(ev, mt)
                self._bus.subscribe("draft", m.name, _draft_handler)

    async def stop(self) -> None:
        for m in self._modules:
            await m.shutdown()

    async def inject_incoming(self, event: IncomingMessage) -> None:
        msg = event.message
        log.debug(
            "inject_incoming chat=%s sender=%s outgoing=%s text=%r",
            msg.chat_id, msg.sender, msg.outgoing, _truncate(msg.text),
        )
        for m in self._modules:
            await self._bus.dispatch(
                "incoming", m.name, chat_id=msg.chat_id, payload=event
            )

    async def inject_draft_update(self, event: DraftUpdate) -> None:
        log.debug(
            "inject_draft_update chat=%s text=%r",
            event.chat_id, _truncate(event.text),
        )
        if self._loop_protect.is_our_write(event.chat_id, event.text):
            log.debug("draft update matches our last write for chat=%s — ignored", event.chat_id)
            return
        match = self._registry.resolve(event.text)
        if match is None:
            log.debug("draft text does not contain any registered marker — ignored")
            return
        log.debug(
            "marker resolved: module=%s name=%s remainder=%r",
            match.module, match.marker.name, _truncate(match.remainder),
        )
        await self._bus.dispatch(
            "draft", match.module, chat_id=event.chat_id, payload=(event, match),
        )

    async def drain(self) -> None:
        await self._bus.drain()

    async def run(self, modules_cfg: dict[str, dict[str, Any]]) -> None:
        """Drive the bus from external client events until cancelled.

        When used with a real TelethonTelegramClient, `tg` is expected to
        have been constructed with on_incoming/on_draft pointing at
        self.inject_incoming / self.inject_draft_update.
        """
        await self.start(modules_cfg)
        await self._tg.connect()
        try:
            await asyncio.Event().wait()
        finally:
            await self._tg.disconnect()
            await self.stop()


def _truncate(text: str, limit: int = 120) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


class _LoopProtectingClient:
    """Wraps a TelegramClient to record our own draft writes for loop protection."""

    def __init__(self, inner: TelegramClient, lp: LoopProtection) -> None:
        self._inner = inner
        self._lp = lp

    async def write_draft(self, chat_id: int, text: str) -> None:
        log.debug("write_draft chat=%s text=%r", chat_id, _truncate(text))
        self._lp.record(chat_id, text)
        await self._inner.write_draft(chat_id, text)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
