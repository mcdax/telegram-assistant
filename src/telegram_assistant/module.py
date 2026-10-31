"""Module protocol and shared context passed at init time."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import aiohttp

    from .events import DraftUpdate, IncomingMessage, MessageEdited, OutgoingMessage
    from .llm import LLMFactory
    from .markers import Marker, MarkerMatch
    from .state import ModuleState
    from .telegram_client import TelegramClient
    from .toggles import Toggle


class AppServices(Protocol):
    """App-level view for modules that span other modules (e.g. settings_menu).

    Both methods are evaluated lazily, so a module may call them after all
    modules have finished loading regardless of its own load order.
    """

    def toggles(self) -> list["Toggle"]: ...
    def module_config(self, name: str) -> dict[str, Any]: ...


@dataclass
class ModuleContext:
    tg: "TelegramClient"
    llm: "LLMFactory"
    http: "aiohttp.ClientSession"
    config: dict[str, Any]
    state: "ModuleState"
    log: logging.Logger
    services: "AppServices | None" = None


@runtime_checkable
class Module(Protocol):
    name: str

    async def init(self, ctx: ModuleContext) -> None: ...
    async def shutdown(self) -> None: ...

    def markers(self) -> list["Marker"]: ...
    def toggles(self) -> list["Toggle"]: ...
    async def on_incoming_message(self, event: "IncomingMessage") -> None: ...
    async def on_message_edited(self, event: "MessageEdited") -> None: ...
    async def on_outgoing_message(self, event: "OutgoingMessage") -> None: ...
    async def on_draft_update(self, event: "DraftUpdate", match: "MarkerMatch") -> None: ...
    async def on_plain_draft_update(self, event: "DraftUpdate") -> None: ...
