"""Loads modules listed in config into a live set plus their markers."""
from __future__ import annotations

import logging
from typing import Any, Callable

from .markers import Marker, MarkerRegistry
from .module import Module, ModuleContext

log = logging.getLogger(__name__)


class UnknownModuleError(KeyError):
    """Raised when config references a module the loader doesn't know about."""


def _known_modules() -> dict[str, type]:
    # Imported lazily to avoid circular imports at module load.
    from .modules.agent.module import AgentModule
    from .modules.correcting.module import CorrectingModule
    from .modules.drafting.module import DraftingModule
    from .modules.media_reply.module import MediaReplyModule
    from .modules.meme.module import MemeModule
    from .modules.settings_menu.module import SettingsMenuModule

    return {
        "agent": AgentModule,
        "drafting": DraftingModule,
        "correcting": CorrectingModule,
        "media_reply": MediaReplyModule,
        "settings_menu": SettingsMenuModule,
        "meme": MemeModule,
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
        ignored_markers: list[str] = []
        for name, cfg in modules_cfg.items():
            if not cfg.get("enabled", False):
                continue
            cls = known.get(name)
            if cls is None:
                raise UnknownModuleError(f"unknown module: {name}")
            instance: Module = cls()
            ctx = context_factory(name, cfg)
            try:
                await instance.init(ctx)
            except Exception as exc:
                log.error("module %r init failed, skipping: %s", name, exc)
                continue
            markers = instance.markers()
            ignored_markers += _unknown_marker_keys(name, cfg, markers)
            registry.register(name, markers)
            loaded.append(instance)
        if ignored_markers:
            log.warning(
                "ignoring unknown/obsolete [modules.*.markers] keys: %s "
                "(per-chat toggles are switched via /settings in the bot chat)",
                ", ".join(ignored_markers),
            )
        return loaded


def _unknown_marker_keys(name: str, cfg: dict[str, Any], markers: list[Marker]) -> list[str]:
    """`<module>.<key>` for override keys in `[modules.<name>.markers]` that no
    registered marker uses, e.g. the removed `auto_draft_on` toggle triggers.
    Modules read overrides by key, so such leftovers are inert; we only warn."""
    overrides = cfg.get("markers")
    if not isinstance(overrides, dict):
        return []
    names = {m.name for m in markers}
    return [f"{name}.{key}" for key in overrides if key not in names]
