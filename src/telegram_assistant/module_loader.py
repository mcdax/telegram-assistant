"""Loads modules listed in config into a live set plus their markers."""
from __future__ import annotations

from typing import Any, Callable

from .markers import MarkerRegistry
from .module import Module, ModuleContext


class UnknownModuleError(KeyError):
    """Raised when config references a module the loader doesn't know about."""


def _known_modules() -> dict[str, type]:
    # Imported lazily to avoid circular imports at module load.
    from .modules.correcting.module import CorrectingModule
    from .modules.drafting.module import DraftingModule
    from .modules.media_reply.module import MediaReplyModule

    return {
        "drafting": DraftingModule,
        "correcting": CorrectingModule,
        "media_reply": MediaReplyModule,
    }


class ModuleLoader:
    async def load(
        self,
        modules_cfg: dict[str, dict[str, Any]],
        registry: MarkerRegistry,
        context_factory: Callable[[str, dict[str, Any]], ModuleContext],
    ) -> list[Module]:
        known = _known_modules()
        loaded: list[Module] = []
        for name, cfg in modules_cfg.items():
            if not cfg.get("enabled", False):
                continue
            cls = known.get(name)
            if cls is None:
                raise UnknownModuleError(f"unknown module: {name}")
            instance: Module = cls()
            ctx = context_factory(name, cfg)
            await instance.init(ctx)
            registry.register(name, instance.markers())
            loaded.append(instance)
        return loaded
