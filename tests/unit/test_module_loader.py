from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from telegram_assistant.markers import DuplicateTriggerError, MarkerRegistry
from telegram_assistant.module import ModuleContext
from telegram_assistant.module_loader import ModuleLoader, UnknownModuleError
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def _build_ctx_factory(tmp_path: Path):
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()

    def make(module_name: str, config: dict[str, Any]) -> ModuleContext:
        return ModuleContext(
            tg=tg,
            llm=fake_llm("X"),
            http=http,
            config=config,
            state=state.for_module(module_name),
            log=logging.getLogger(module_name),
        )

    return make, http


async def test_loads_enabled_modules(tmp_path: Path):
    make, http = await _build_ctx_factory(tmp_path)
    loader = ModuleLoader()
    registry = MarkerRegistry()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "x",
            "last_n": 10,
            "auto_draft_chats": [],
            "enrichment_url": "",
            "markers": {},
        },
        "correcting": {"enabled": True, "system_prompt": "x"},
    }
    loaded = await loader.load(modules_cfg, registry, make)
    names = {m.name for m in loaded}
    assert names == {"drafting", "correcting"}
    await http.close()


async def test_skips_disabled(tmp_path: Path):
    make, http = await _build_ctx_factory(tmp_path)
    loader = ModuleLoader()
    registry = MarkerRegistry()
    modules_cfg = {
        "drafting": {"enabled": False, "default_system_prompt": "x", "last_n": 10, "auto_draft_chats": [], "enrichment_url": "", "markers": {}},
        "correcting": {"enabled": True, "system_prompt": "x"},
    }
    loaded = await loader.load(modules_cfg, registry, make)
    assert [m.name for m in loaded] == ["correcting"]
    await http.close()


async def test_unknown_module_raises(tmp_path: Path):
    make, http = await _build_ctx_factory(tmp_path)
    loader = ModuleLoader()
    registry = MarkerRegistry()
    with pytest.raises(UnknownModuleError):
        await loader.load({"nope": {"enabled": True}}, registry, make)
    await http.close()


async def test_markers_collected_into_registry(tmp_path: Path):
    make, http = await _build_ctx_factory(tmp_path)
    loader = ModuleLoader()
    registry = MarkerRegistry()
    await loader.load(
        {
            "drafting": {
                "enabled": True,
                "default_system_prompt": "x",
                "last_n": 10,
                "auto_draft_chats": [],
                "enrichment_url": "",
                "markers": {},
            },
            "correcting": {"enabled": True, "system_prompt": "x"},
        },
        registry,
        make,
    )
    assert registry.resolve("/fix hi") is not None
    assert registry.resolve("/draft") is not None
    assert registry.resolve("/auto on") is not None
    await http.close()


async def test_duplicate_marker_across_modules_raises(tmp_path: Path):
    make, http = await _build_ctx_factory(tmp_path)
    loader = ModuleLoader()
    registry = MarkerRegistry()
    with pytest.raises(DuplicateTriggerError):
        await loader.load(
            {
                "drafting": {
                    "enabled": True,
                    "default_system_prompt": "x",
                    "last_n": 10,
                    "auto_draft_chats": [],
                    "enrichment_url": "",
                    "markers": {"draft": "/x"},
                },
                "correcting": {
                    "enabled": True,
                    "system_prompt": "x",
                    "markers": {"fix": "/x"},
                },
            },
            registry,
            make,
        )
    await http.close()
