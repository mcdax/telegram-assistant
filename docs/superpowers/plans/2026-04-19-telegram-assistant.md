# Telegram Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a modular Python userbot for Telegram (MTProto) that lets plugin modules react to incoming messages and draft markers. Initial modules: `drafting` (LLM reply drafts + `/auto on|off` toggle), `correcting` (`/fix` rewrites), `media_reply` (URL-regex → yt-dlp download → reply).

**Architecture:** Single-process asyncio app with a small core (Telegram client, event bus, marker registry, config/state, LLM factory, module loader) and discrete modules that register handlers. Markers are configurable per module via `config.toml`. Runtime state persists in app-managed `state.toml`. No database.

**Tech Stack:** Python 3.12, `uv`, `telethon`, `pydantic-ai`, `aiohttp`, `watchfiles`, `tomllib` / `tomli-w`, `yt-dlp`, `pytest`, `pytest-asyncio`, `aioresponses`.

**Spec:** `docs/superpowers/specs/2026-04-19-telegram-assistant-design.md`.

---

## File Layout (target)

```
telegram-assistant/
├── pyproject.toml
├── config.example.toml
├── .gitignore
├── src/telegram_assistant/
│   ├── __init__.py
│   ├── __main__.py
│   ├── app.py
│   ├── config.py
│   ├── state.py
│   ├── loop_protection.py
│   ├── events.py
│   ├── markers.py
│   ├── event_bus.py
│   ├── module.py
│   ├── module_loader.py
│   ├── llm.py
│   ├── telegram_client.py
│   ├── telethon_client.py
│   └── modules/
│       ├── __init__.py
│       ├── drafting/
│       │   ├── __init__.py
│       │   ├── module.py
│       │   ├── pipeline.py
│       │   └── enricher.py
│       ├── correcting/
│       │   ├── __init__.py
│       │   └── module.py
│       └── media_reply/
│           ├── __init__.py
│           ├── module.py
│           └── backends.py
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── fakes/
    │   ├── __init__.py
    │   ├── telegram.py
    │   └── llm.py
    ├── unit/
    │   ├── test_config.py
    │   ├── test_state.py
    │   ├── test_loop_protection.py
    │   ├── test_markers.py
    │   ├── test_event_bus.py
    │   ├── test_module_loader.py
    │   ├── test_llm.py
    │   └── modules/
    │       ├── drafting/
    │       │   ├── test_enricher.py
    │       │   ├── test_pipeline.py
    │       │   └── test_module.py
    │       ├── correcting/
    │       │   └── test_module.py
    │       └── media_reply/
    │           ├── test_backends.py
    │           └── test_module.py
    └── integration/
        ├── test_drafting_flow.py
        ├── test_correcting_flow.py
        ├── test_auto_toggle_flow.py
        └── test_media_reply_flow.py
```

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `config.example.toml`
- Create: `src/telegram_assistant/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "telegram-assistant"
version = "0.1.0"
description = "Modular Telegram userbot with pluggable features."
requires-python = ">=3.12"
dependencies = [
    "telethon>=1.34",
    "pydantic-ai>=0.0.14",
    "aiohttp>=3.9",
    "watchfiles>=0.22",
    "tomli-w>=1.0",
    "yt-dlp>=2024.8.0",
]

[project.scripts]
telegram-assistant = "telegram_assistant.__main__:main"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "aioresponses>=0.7.6",
    "pytest-mock>=3.12",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/telegram_assistant"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Write `.gitignore`**

```
# Secrets and runtime state
config.toml
state.toml
*.session
*.session-journal

# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
dist/
build/
```

- [ ] **Step 3: Write `config.example.toml`**

```toml
[telegram]
api_id = 0
api_hash = "YOUR_API_HASH"
session = "assistant"

[llm]
model = "anthropic:claude-sonnet-4-6"
timeout_s = 30

[modules.drafting]
enabled = true
default_system_prompt = "You are drafting a reply in the user's voice. Casual, concise, matches the conversation's tone."
last_n = 20
auto_draft_chats = []
enrichment_url = ""
enrichment_auth_header = ""
enrichment_timeout_s = 10

[modules.drafting.markers]
draft = "/draft"
auto_on = "/auto on"
auto_off = "/auto off"

[modules.correcting]
enabled = true
system_prompt = "Rewrite the given text correcting grammar, spelling, and punctuation. Preserve meaning, tone, and language. Output only the rewritten text."

[modules.correcting.markers]
fix = "/fix"

[modules.media_reply]
enabled = true
chats = []
send_as = "reply"
download_timeout_s = 60

[[modules.media_reply.handlers]]
name = "instagram"
pattern = 'https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[\w-]+'
backend = "yt_dlp"
```

- [ ] **Step 4: Create empty init files**

```bash
mkdir -p src/telegram_assistant tests
touch src/telegram_assistant/__init__.py
touch tests/__init__.py
```

- [ ] **Step 5: Write minimal `tests/conftest.py`**

```python
"""Test configuration. Fixtures shared across unit and integration tests are added as needed."""
```

- [ ] **Step 6: Install and smoke-test**

```bash
uv sync
uv run pytest --collect-only
```

Expected: `no tests ran` (but no import errors).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore config.example.toml src tests
git commit -m "chore: scaffold project with pyproject, deps, gitignore, example config"
```

---

## Task 2: Config parser (static)

Parse `config.toml` into typed dataclasses. No hot-reload yet.

**Files:**
- Create: `src/telegram_assistant/config.py`
- Create: `tests/unit/test_config.py`
- Modify: `tests/conftest.py` (add helper for writing temp TOML)

- [ ] **Step 1: Add the `write_toml` helper to `tests/conftest.py`**

```python
"""Test configuration."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def write_toml(tmp_path: Path):
    def _write(content: str, name: str = "config.toml") -> Path:
        p = tmp_path / name
        p.write_text(content, encoding="utf-8")
        return p
    return _write
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_config.py`:

```python
from __future__ import annotations

import pytest

from telegram_assistant.config import Config, ConfigError, load_config


def test_parses_minimal_config(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 123
        api_hash = "abc"
        session = "s"

        [llm]
        model = "test:model"
        timeout_s = 42

        [modules.drafting]
        enabled = true
        """
    )
    cfg = load_config(path)
    assert isinstance(cfg, Config)
    assert cfg.telegram.api_id == 123
    assert cfg.telegram.api_hash == "abc"
    assert cfg.telegram.session == "s"
    assert cfg.llm.model == "test:model"
    assert cfg.llm.timeout_s == 42
    assert cfg.modules["drafting"]["enabled"] is True


def test_missing_telegram_section_raises(write_toml):
    path = write_toml(
        """
        [llm]
        model = "x"
        timeout_s = 10
        """
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_llm_section_raises(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"
        """
    )
    with pytest.raises(ConfigError):
        load_config(path)


def test_modules_section_optional(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    cfg = load_config(path)
    assert cfg.modules == {}


def test_module_config_preserved_verbatim(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"

        [llm]
        model = "m"
        timeout_s = 10

        [modules.drafting]
        enabled = true
        last_n = 20

        [modules.drafting.markers]
        draft = "!d"
        """
    )
    cfg = load_config(path)
    assert cfg.modules["drafting"]["last_n"] == 20
    assert cfg.modules["drafting"]["markers"]["draft"] == "!d"


def test_invalid_toml_raises(write_toml):
    path = write_toml("this is = not valid = toml")
    with pytest.raises(ConfigError):
        load_config(path)


def test_file_not_found_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "missing.toml")
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: import error or collection failure (`telegram_assistant.config` doesn't exist).

- [ ] **Step 4: Implement `src/telegram_assistant/config.py`**

```python
"""Config loader. Reads and validates config.toml into typed dataclasses."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when config.toml is missing, invalid, or incomplete."""


@dataclass(frozen=True)
class TelegramConfig:
    api_id: int
    api_hash: str
    session: str


@dataclass(frozen=True)
class LLMConfig:
    model: str
    timeout_s: int


@dataclass(frozen=True)
class Config:
    telegram: TelegramConfig
    llm: LLMConfig
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_config(path: Path) -> Config:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as e:
        raise ConfigError(f"config file not found: {path}") from e

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e

    return _parse(data)


def _parse(data: dict[str, Any]) -> Config:
    if "telegram" not in data:
        raise ConfigError("missing [telegram] section")
    if "llm" not in data:
        raise ConfigError("missing [llm] section")

    t = data["telegram"]
    for key in ("api_id", "api_hash", "session"):
        if key not in t:
            raise ConfigError(f"missing telegram.{key}")

    l = data["llm"]
    for key in ("model", "timeout_s"):
        if key not in l:
            raise ConfigError(f"missing llm.{key}")

    modules = data.get("modules", {})
    if not isinstance(modules, dict):
        raise ConfigError("[modules] must be a table")

    return Config(
        telegram=TelegramConfig(
            api_id=int(t["api_id"]),
            api_hash=str(t["api_hash"]),
            session=str(t["session"]),
        ),
        llm=LLMConfig(
            model=str(l["model"]),
            timeout_s=int(l["timeout_s"]),
        ),
        modules=dict(modules),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_config.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/telegram_assistant/config.py tests/unit/test_config.py tests/conftest.py
git commit -m "feat(config): add typed config loader for config.toml"
```

---

## Task 3: Runtime state (state.toml read/write)

Namespaced, atomic-write state file.

**Files:**
- Create: `src/telegram_assistant/state.py`
- Create: `tests/unit/test_state.py`

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

from telegram_assistant.state import RuntimeState


def test_get_default_when_absent(tmp_path: Path):
    state = RuntimeState(tmp_path / "state.toml")
    ns = state.for_module("drafting")
    assert ns.get("auto_draft", "12345", default=False) is False


def test_set_and_get(tmp_path: Path):
    state = RuntimeState(tmp_path / "state.toml")
    ns = state.for_module("drafting")
    ns.set("auto_draft", "12345", True)
    assert ns.get("auto_draft", "12345", default=False) is True


def test_persists_across_reload(tmp_path: Path):
    path = tmp_path / "state.toml"
    state = RuntimeState(path)
    state.for_module("drafting").set("auto_draft", "12345", True)

    reloaded = RuntimeState(path)
    assert reloaded.for_module("drafting").get("auto_draft", "12345", default=False) is True


def test_namespaces_isolated(tmp_path: Path):
    state = RuntimeState(tmp_path / "state.toml")
    state.for_module("drafting").set("flag", "a", True)
    state.for_module("other").set("flag", "a", False)
    assert state.for_module("drafting").get("flag", "a", default=None) is True
    assert state.for_module("other").get("flag", "a", default=None) is False


def test_atomic_write_uses_temp_file(tmp_path: Path):
    path = tmp_path / "state.toml"
    state = RuntimeState(path)
    state.for_module("drafting").set("auto_draft", "12345", True)
    # After a successful set, exactly one file should exist at the final path.
    assert path.exists()
    assert not (tmp_path / "state.toml.tmp").exists()


def test_missing_file_loads_empty(tmp_path: Path):
    state = RuntimeState(tmp_path / "does_not_exist.toml")
    assert state.for_module("drafting").get("auto_draft", "1", default="X") == "X"
```

- [ ] **Step 2: Verify tests fail**

```bash
uv run pytest tests/unit/test_state.py -v
```

Expected: import error.

- [ ] **Step 3: Implement `src/telegram_assistant/state.py`**

```python
"""Runtime state persisted in state.toml. Namespaced per module.

The app is the sole writer. Writes are atomic (temp + rename).
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w


class RuntimeState:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return {}
        return tomllib.loads(raw.decode("utf-8"))

    def _write(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("wb") as f:
            tomli_w.dump(self._data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)

    def for_module(self, module_name: str) -> "ModuleState":
        return ModuleState(self, module_name)

    def _get(self, module: str, bucket: str, key: str, default: Any) -> Any:
        return self._data.get(module, {}).get(bucket, {}).get(key, default)

    def _set(self, module: str, bucket: str, key: str, value: Any) -> None:
        self._data.setdefault(module, {}).setdefault(bucket, {})[key] = value
        self._write()


class ModuleState:
    def __init__(self, root: RuntimeState, module: str) -> None:
        self._root = root
        self._module = module

    def get(self, bucket: str, key: str, default: Any) -> Any:
        return self._root._get(self._module, bucket, key, default)

    def set(self, bucket: str, key: str, value: Any) -> None:
        self._root._set(self._module, bucket, key, value)
```

- [ ] **Step 4: Run and verify tests pass**

```bash
uv run pytest tests/unit/test_state.py -v
```

Expected: all 6 pass.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/state.py tests/unit/test_state.py
git commit -m "feat(state): add namespaced atomic-write runtime state"
```

---

## Task 4: Loop protection helper

Tracks the last draft text this app wrote per chat, to ignore echoes from our own `SaveDraft` calls.

**Files:**
- Create: `src/telegram_assistant/loop_protection.py`
- Create: `tests/unit/test_loop_protection.py`

- [ ] **Step 1: Write failing tests**

```python
from telegram_assistant.loop_protection import LoopProtection


def test_initially_not_our_write():
    lp = LoopProtection()
    assert lp.is_our_write(chat_id=1, text="hello") is False


def test_record_then_match():
    lp = LoopProtection()
    lp.record(chat_id=1, text="hello")
    assert lp.is_our_write(chat_id=1, text="hello") is True


def test_record_does_not_match_different_text():
    lp = LoopProtection()
    lp.record(chat_id=1, text="hello")
    assert lp.is_our_write(chat_id=1, text="world") is False


def test_independent_chats():
    lp = LoopProtection()
    lp.record(chat_id=1, text="hello")
    assert lp.is_our_write(chat_id=2, text="hello") is False
```

- [ ] **Step 2: Run tests — expect import error**

```bash
uv run pytest tests/unit/test_loop_protection.py -v
```

- [ ] **Step 3: Implement**

```python
"""Loop protection. Tracks last draft text this app wrote per chat."""
from __future__ import annotations


class LoopProtection:
    def __init__(self) -> None:
        self._last: dict[int, str] = {}

    def record(self, chat_id: int, text: str) -> None:
        self._last[chat_id] = text

    def is_our_write(self, chat_id: int, text: str) -> bool:
        return self._last.get(chat_id) == text
```

- [ ] **Step 4: Run tests — expect pass**

```bash
uv run pytest tests/unit/test_loop_protection.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/loop_protection.py tests/unit/test_loop_protection.py
git commit -m "feat(core): add loop protection helper"
```

---

## Task 5: Event dataclasses and Marker

Define the event payloads and the `Marker` value object used by modules and the registry.

**Files:**
- Create: `src/telegram_assistant/events.py`
- Create: `src/telegram_assistant/markers.py`
- Create: `tests/unit/test_markers.py`

- [ ] **Step 1: Write `src/telegram_assistant/events.py`**

```python
"""Event types emitted onto the event bus."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Message:
    chat_id: int
    message_id: int
    sender: str
    timestamp: datetime
    text: str
    outgoing: bool


@dataclass(frozen=True)
class IncomingMessage:
    message: Message


@dataclass(frozen=True)
class DraftUpdate:
    chat_id: int
    text: str
```

- [ ] **Step 2: Write failing tests for markers**

```python
from telegram_assistant.markers import Marker, MatchKind


def test_exact_match_case_insensitive_trimmed():
    m = Marker(name="auto_on", trigger="/auto on", kind=MatchKind.EXACT, priority=100)
    ok, remainder = m.match("  /AUTO on  ")
    assert ok is True
    assert remainder == ""


def test_exact_match_rejects_extra_content():
    m = Marker(name="auto_on", trigger="/auto on", kind=MatchKind.EXACT, priority=100)
    ok, remainder = m.match("/auto on now")
    assert ok is False
    assert remainder is None


def test_contains_match_strips_marker_and_returns_remainder():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("please /draft and ask about tomorrow")
    assert ok is True
    assert remainder == "please and ask about tomorrow"


def test_contains_match_empty_remainder():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("/draft")
    assert ok is True
    assert remainder == ""


def test_no_match():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("hello world")
    assert ok is False
    assert remainder is None


def test_contains_takes_first_occurrence():
    m = Marker(name="draft", trigger="/draft", kind=MatchKind.CONTAINS, priority=50)
    ok, remainder = m.match("a /draft b /draft c")
    assert ok is True
    # Only first occurrence is stripped; subsequent stays in the remainder.
    assert remainder == "a b /draft c"
```

- [ ] **Step 3: Run, expect import error**

```bash
uv run pytest tests/unit/test_markers.py -v
```

- [ ] **Step 4: Implement `src/telegram_assistant/markers.py`**

```python
"""Marker definitions and registry.

A Marker is a trigger string a module registers. On a DraftUpdate,
the registry selects at most one winning marker (highest priority, ties
broken by registration order) and the matching module handles the event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MatchKind(Enum):
    EXACT = "exact"
    CONTAINS = "contains"


@dataclass(frozen=True)
class Marker:
    name: str
    trigger: str
    kind: MatchKind
    priority: int

    def match(self, text: str) -> tuple[bool, Optional[str]]:
        """Return (matched, remainder_if_matched)."""
        if self.kind is MatchKind.EXACT:
            if text.strip().casefold() == self.trigger.casefold():
                return True, ""
            return False, None
        # CONTAINS: case-sensitive, first occurrence stripped.
        idx = text.find(self.trigger)
        if idx < 0:
            return False, None
        before = text[:idx].rstrip()
        after = text[idx + len(self.trigger) :].lstrip()
        if before and after:
            remainder = f"{before} {after}"
        else:
            remainder = before + after
        return True, remainder
```

- [ ] **Step 5: Run tests, expect pass**

```bash
uv run pytest tests/unit/test_markers.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/telegram_assistant/events.py src/telegram_assistant/markers.py tests/unit/test_markers.py
git commit -m "feat(core): add event dataclasses and Marker value object"
```

---

## Task 6: MarkerRegistry

Resolves which module handles a given draft text.

**Files:**
- Modify: `src/telegram_assistant/markers.py` (append)
- Modify: `tests/unit/test_markers.py` (append)

- [ ] **Step 1: Append failing tests**

```python
from telegram_assistant.markers import MarkerRegistry, DuplicateTriggerError


def test_registry_returns_winning_marker():
    reg = MarkerRegistry()
    reg.register("drafting", [
        Marker("draft", "/draft", MatchKind.CONTAINS, priority=50),
        Marker("auto_on", "/auto on", MatchKind.EXACT, priority=100),
    ])
    match = reg.resolve("/auto on")
    assert match is not None
    assert match.module == "drafting"
    assert match.marker.name == "auto_on"
    assert match.remainder == ""


def test_registry_priority_beats_lower():
    reg = MarkerRegistry()
    reg.register("drafting", [Marker("draft", "/draft", MatchKind.CONTAINS, priority=50)])
    reg.register("correcting", [Marker("fix", "/fix", MatchKind.CONTAINS, priority=70)])
    match = reg.resolve("hello /draft /fix world")
    assert match is not None
    assert match.module == "correcting"
    assert match.marker.name == "fix"


def test_registry_no_match_returns_none():
    reg = MarkerRegistry()
    reg.register("drafting", [Marker("draft", "/draft", MatchKind.CONTAINS, priority=50)])
    assert reg.resolve("hello world") is None


def test_registry_duplicate_trigger_rejected_across_modules():
    reg = MarkerRegistry()
    reg.register("drafting", [Marker("draft", "/x", MatchKind.CONTAINS, priority=50)])
    import pytest
    with pytest.raises(DuplicateTriggerError):
        reg.register("correcting", [Marker("fix", "/x", MatchKind.CONTAINS, priority=70)])


def test_registry_duplicate_trigger_rejected_same_module():
    reg = MarkerRegistry()
    import pytest
    with pytest.raises(DuplicateTriggerError):
        reg.register("drafting", [
            Marker("a", "/x", MatchKind.CONTAINS, priority=50),
            Marker("b", "/x", MatchKind.EXACT, priority=90),
        ])
```

- [ ] **Step 2: Run, expect import errors**

```bash
uv run pytest tests/unit/test_markers.py -v
```

- [ ] **Step 3: Append implementation to `src/telegram_assistant/markers.py`**

```python
from dataclasses import dataclass as _dataclass


class DuplicateTriggerError(ValueError):
    """Raised when two markers share the same trigger string."""


@_dataclass(frozen=True)
class MarkerMatch:
    module: str
    marker: Marker
    remainder: str


class MarkerRegistry:
    def __init__(self) -> None:
        self._entries: list[tuple[str, Marker]] = []

    def register(self, module_name: str, markers: list[Marker]) -> None:
        existing_triggers = {m.trigger for _, m in self._entries}
        seen_in_batch: set[str] = set()
        for m in markers:
            if m.trigger in existing_triggers or m.trigger in seen_in_batch:
                raise DuplicateTriggerError(
                    f"duplicate marker trigger {m.trigger!r} (module={module_name})"
                )
            seen_in_batch.add(m.trigger)
        for m in markers:
            self._entries.append((module_name, m))

    def resolve(self, text: str) -> MarkerMatch | None:
        candidates: list[MarkerMatch] = []
        for module_name, marker in self._entries:
            ok, remainder = marker.match(text)
            if ok:
                assert remainder is not None
                candidates.append(MarkerMatch(module_name, marker, remainder))
        if not candidates:
            return None
        candidates.sort(key=lambda c: -c.marker.priority)
        return candidates[0]
```

- [ ] **Step 4: Run tests, expect pass**

```bash
uv run pytest tests/unit/test_markers.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/markers.py tests/unit/test_markers.py
git commit -m "feat(core): add MarkerRegistry with priority resolution and duplicate rejection"
```

---

## Task 7: EventBus with per-(module, chat_id) cancellation

Concurrent dispatch; a new event targeting a `(module, chat_id)` pair cancels the in-flight handler for that pair.

**Files:**
- Create: `src/telegram_assistant/event_bus.py`
- Create: `tests/unit/test_event_bus.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import asyncio
import pytest

from telegram_assistant.event_bus import EventBus


async def test_subscribe_and_dispatch():
    bus = EventBus()
    calls = []

    async def handler(event: str) -> None:
        calls.append(event)

    bus.subscribe("topic", "modA", handler)
    await bus.dispatch("topic", "modA", chat_id=1, payload="hello")
    await bus.drain()
    assert calls == ["hello"]


async def test_handler_exception_isolated_and_logged(caplog):
    bus = EventBus()
    ok_calls = []

    async def bad(event):
        raise RuntimeError("boom")

    async def ok(event):
        ok_calls.append(event)

    bus.subscribe("t", "bad", bad)
    bus.subscribe("t", "ok", ok)
    await bus.dispatch("t", "bad", chat_id=1, payload="x")
    await bus.dispatch("t", "ok", chat_id=1, payload="x")
    await bus.drain()
    assert ok_calls == ["x"]
    assert any("boom" in rec.message for rec in caplog.records)


async def test_inflight_cancelled_for_same_pair():
    bus = EventBus()
    first_started = asyncio.Event()
    cancelled = False

    async def slow(event):
        nonlocal cancelled
        first_started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled = True
            raise

    bus.subscribe("t", "m", slow)
    await bus.dispatch("t", "m", chat_id=1, payload="first")
    await first_started.wait()
    await bus.dispatch("t", "m", chat_id=1, payload="second")
    await bus.drain()
    assert cancelled is True


async def test_different_pairs_run_concurrently():
    bus = EventBus()
    running = 0
    peak = 0

    async def handler(event):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1

    bus.subscribe("t", "m", handler)
    await bus.dispatch("t", "m", chat_id=1, payload="a")
    await bus.dispatch("t", "m", chat_id=2, payload="b")
    await bus.drain()
    assert peak == 2
```

- [ ] **Step 2: Run, expect import error**

```bash
uv run pytest tests/unit/test_event_bus.py -v
```

- [ ] **Step 3: Implement `src/telegram_assistant/event_bus.py`**

```python
"""Event bus with per-(module, chat_id) task tracking.

dispatch() schedules a handler invocation. If another invocation for the
same (module, chat_id) is still running, it is cancelled first.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

Handler = Callable[[Any], Awaitable[None]]
log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], Handler] = {}
        self._tasks: dict[tuple[str, str, int], asyncio.Task] = {}
        self._all_tasks: set[asyncio.Task] = set()

    def subscribe(self, topic: str, module: str, handler: Handler) -> None:
        self._subs[(topic, module)] = handler

    async def dispatch(self, topic: str, module: str, chat_id: int, payload: Any) -> None:
        handler = self._subs.get((topic, module))
        if handler is None:
            return

        key = (topic, module, chat_id)
        prev = self._tasks.get(key)
        if prev is not None and not prev.done():
            prev.cancel()
            try:
                await prev
            except (asyncio.CancelledError, Exception):
                pass

        async def _run() -> None:
            try:
                await handler(payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("handler for (%s, %s) raised: %s", topic, module, e, exc_info=True)

        task = asyncio.create_task(_run())
        self._tasks[key] = task
        self._all_tasks.add(task)
        task.add_done_callback(self._all_tasks.discard)

    async def drain(self) -> None:
        pending = list(self._all_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
uv run pytest tests/unit/test_event_bus.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/event_bus.py tests/unit/test_event_bus.py
git commit -m "feat(core): add event bus with per-(module, chat_id) cancellation"
```

---

## Task 8: Module Protocol and ModuleContext

Pure type definitions. No behaviour yet.

**Files:**
- Create: `src/telegram_assistant/module.py`

- [ ] **Step 1: Write `src/telegram_assistant/module.py`**

```python
"""Module protocol and shared context passed at init time."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import aiohttp

    from .events import DraftUpdate, IncomingMessage
    from .llm import LLMFactory
    from .markers import Marker, MarkerMatch
    from .state import ModuleState
    from .telegram_client import TelegramClient


@dataclass
class ModuleContext:
    tg: "TelegramClient"
    llm: "LLMFactory"
    http: "aiohttp.ClientSession"
    config: dict[str, Any]
    state: "ModuleState"
    log: logging.Logger


@runtime_checkable
class Module(Protocol):
    name: str

    async def init(self, ctx: ModuleContext) -> None: ...
    async def shutdown(self) -> None: ...

    def markers(self) -> list["Marker"]: ...
    async def on_incoming_message(self, event: "IncomingMessage") -> None: ...
    async def on_draft_update(self, event: "DraftUpdate", match: "MarkerMatch") -> None: ...
```

- [ ] **Step 2: Verify imports by collecting tests**

```bash
uv run pytest --collect-only
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_assistant/module.py
git commit -m "feat(core): add Module protocol and ModuleContext"
```

---

## Task 9: LLMFactory

Thin wrapper around Pydantic AI. Provides `agent(system_prompt)` and `run(agent, user_text)` with a timeout. Tests use Pydantic AI's `TestModel`.

**Files:**
- Create: `src/telegram_assistant/llm.py`
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/llm.py`
- Create: `tests/unit/test_llm.py`

- [ ] **Step 1: Create `tests/fakes/__init__.py` (empty)**

```python
```

- [ ] **Step 2: Create `tests/fakes/llm.py`**

```python
"""Test helpers for building LLM factories that return canned responses."""
from __future__ import annotations

from pydantic_ai.models.test import TestModel

from telegram_assistant.llm import LLMFactory


def fake_llm(response: str, *, timeout_s: int = 5) -> LLMFactory:
    return LLMFactory(model=TestModel(custom_output_text=response), timeout_s=timeout_s)
```

- [ ] **Step 3: Write failing tests**

```python
from __future__ import annotations

import pytest

from tests.fakes.llm import fake_llm


async def test_agent_runs_and_returns_text():
    llm = fake_llm("generated reply")
    agent = llm.agent("you are a helper")
    out = await llm.run(agent, "hello")
    assert out == "generated reply"


async def test_agent_timeout_raises():
    import asyncio

    from telegram_assistant.llm import LLMFactory, LLMTimeout

    class SlowModel:
        async def request(self, *a, **kw):
            await asyncio.sleep(5)

    llm = LLMFactory(model=SlowModel(), timeout_s=0)  # type: ignore[arg-type]
    agent = llm.agent("sys")
    with pytest.raises(LLMTimeout):
        await llm.run(agent, "u")
```

- [ ] **Step 4: Run, expect import errors**

```bash
uv run pytest tests/unit/test_llm.py -v
```

- [ ] **Step 5: Implement `src/telegram_assistant/llm.py`**

```python
"""LLM factory. Thin wrapper over pydantic-ai."""
from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import Agent


class LLMTimeout(TimeoutError):
    pass


class LLMFactory:
    def __init__(self, model: Any, timeout_s: int) -> None:
        self._model = model
        self._timeout_s = timeout_s

    def agent(self, system_prompt: str) -> Agent[None, str]:
        return Agent(self._model, system_prompt=system_prompt, output_type=str)

    async def run(self, agent: Agent[None, str], user_text: str) -> str:
        try:
            result = await asyncio.wait_for(agent.run(user_text), timeout=self._timeout_s)
        except asyncio.TimeoutError as e:
            raise LLMTimeout(f"LLM call exceeded {self._timeout_s}s") from e
        return str(result.output)
```

- [ ] **Step 6: Run tests, expect pass**

```bash
uv run pytest tests/unit/test_llm.py -v
```

(If `TestModel.custom_output_text` doesn't exist in the installed pydantic-ai version, substitute the class's documented API to return canned text — see `pydantic_ai.models.test.TestModel`. The principle is: tests never hit a real LLM.)

- [ ] **Step 7: Commit**

```bash
git add src/telegram_assistant/llm.py tests/fakes/__init__.py tests/fakes/llm.py tests/unit/test_llm.py
git commit -m "feat(core): add LLMFactory wrapping pydantic-ai with timeout"
```

---

## Task 10: TelegramClient protocol and fake

Protocol-level definition plus an in-memory fake used throughout module tests.

**Files:**
- Create: `src/telegram_assistant/telegram_client.py`
- Create: `tests/fakes/telegram.py`

- [ ] **Step 1: Write `src/telegram_assistant/telegram_client.py`**

```python
"""Telegram client protocol. Concrete implementation is in telethon_client.py."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .events import Message


class TelegramClient(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        files: list[Path] | None = None,
    ) -> None: ...

    async def write_draft(self, chat_id: int, text: str) -> None: ...

    async def fetch_history(self, chat_id: int, n: int) -> list[Message]: ...

    async def download_media(self, message_id: int, chat_id: int, dest_dir: Path) -> Path: ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
```

- [ ] **Step 2: Write `tests/fakes/telegram.py`**

```python
"""In-memory TelegramClient for tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from telegram_assistant.events import Message


@dataclass
class SentMessage:
    chat_id: int
    text: str | None
    reply_to: int | None
    files: list[Path]


@dataclass
class FakeTelegramClient:
    drafts: dict[int, str] = field(default_factory=dict)
    sent: list[SentMessage] = field(default_factory=list)
    history: dict[int, list[Message]] = field(default_factory=dict)

    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        files: list[Path] | None = None,
    ) -> None:
        self.sent.append(SentMessage(chat_id, text, reply_to, list(files or [])))

    async def write_draft(self, chat_id: int, text: str) -> None:
        self.drafts[chat_id] = text

    async def fetch_history(self, chat_id: int, n: int) -> list[Message]:
        return list(self.history.get(chat_id, []))[-n:]

    async def download_media(self, message_id: int, chat_id: int, dest_dir: Path) -> Path:
        p = dest_dir / f"msg-{message_id}.bin"
        p.write_bytes(b"fake-media")
        return p

    async def connect(self) -> None:
        return

    async def disconnect(self) -> None:
        return

    def seed_history(self, chat_id: int, messages: list[Message]) -> None:
        self.history[chat_id] = list(messages)


def make_message(
    chat_id: int,
    sender: str,
    text: str,
    message_id: int = 1,
    outgoing: bool = False,
) -> Message:
    return Message(
        chat_id=chat_id,
        message_id=message_id,
        sender=sender,
        timestamp=datetime(2026, 4, 19, 10, 0, tzinfo=timezone.utc),
        text=text,
        outgoing=outgoing,
    )
```

- [ ] **Step 3: Verify imports**

```bash
uv run pytest --collect-only
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add src/telegram_assistant/telegram_client.py tests/fakes/telegram.py
git commit -m "feat(core): add TelegramClient protocol and in-memory fake"
```

---

## Task 11: Enricher (drafting submodule)

HTTP call to the external context endpoint. Best-effort.

**Files:**
- Create: `src/telegram_assistant/modules/__init__.py`
- Create: `src/telegram_assistant/modules/drafting/__init__.py`
- Create: `src/telegram_assistant/modules/drafting/enricher.py`
- Create: `tests/unit/modules/__init__.py`
- Create: `tests/unit/modules/drafting/__init__.py`
- Create: `tests/unit/modules/drafting/test_enricher.py`

- [ ] **Step 1: Create empty init files**

```bash
mkdir -p src/telegram_assistant/modules/drafting
touch src/telegram_assistant/modules/__init__.py
touch src/telegram_assistant/modules/drafting/__init__.py
mkdir -p tests/unit/modules/drafting
touch tests/unit/modules/__init__.py
touch tests/unit/modules/drafting/__init__.py
```

- [ ] **Step 2: Write failing tests**

```python
from __future__ import annotations

import aiohttp
from aioresponses import aioresponses

from telegram_assistant.events import Message
from telegram_assistant.modules.drafting.enricher import Enricher
from tests.fakes.telegram import make_message


MSG: list[Message] = [make_message(chat_id=1, sender="alice", text="hi", message_id=1)]


async def test_enricher_happy_path():
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post(
                "https://host/ctx",
                status=200,
                payload={"context": "it is 5pm"},
            )
            enricher = Enricher(
                http=http,
                url="https://host/ctx",
                auth_header=None,
                timeout_s=5,
            )
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == "it is 5pm"


async def test_enricher_non_2xx_returns_empty(caplog):
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post("https://host/ctx", status=500)
            enricher = Enricher(http=http, url="https://host/ctx", auth_header=None, timeout_s=5)
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == ""


async def test_enricher_timeout_returns_empty():
    import asyncio
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            async def slow(*a, **kw):
                await asyncio.sleep(5)
                return aiohttp.web.Response()

            mock.post("https://host/ctx", callback=slow)
            enricher = Enricher(http=http, url="https://host/ctx", auth_header=None, timeout_s=0)
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == ""


async def test_enricher_network_error_returns_empty():
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post("https://host/ctx", exception=aiohttp.ClientConnectorError(None, OSError("x")))
            enricher = Enricher(http=http, url="https://host/ctx", auth_header=None, timeout_s=5)
            out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            assert out == ""


async def test_enricher_empty_url_returns_empty():
    async with aiohttp.ClientSession() as http:
        enricher = Enricher(http=http, url="", auth_header=None, timeout_s=5)
        out = await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
        assert out == ""


async def test_enricher_auth_header_passed():
    async with aiohttp.ClientSession() as http:
        with aioresponses() as mock:
            mock.post("https://host/ctx", status=200, payload={"context": "ok"})
            enricher = Enricher(
                http=http, url="https://host/ctx", auth_header="Bearer X", timeout_s=5
            )
            await enricher.fetch(chat_id=1, chat_title="x", messages=MSG)
            req = list(mock.requests.values())[0][0]
            assert req.kwargs["headers"]["Authorization"] == "Bearer X"
```

- [ ] **Step 3: Run, expect import error**

```bash
uv run pytest tests/unit/modules/drafting/test_enricher.py -v
```

- [ ] **Step 4: Implement `src/telegram_assistant/modules/drafting/enricher.py`**

```python
"""External context enrichment. Best-effort HTTP call.

Failures (timeout, non-2xx, network error, empty URL) all yield "".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable

import aiohttp

from telegram_assistant.events import Message

log = logging.getLogger(__name__)


class Enricher:
    def __init__(
        self,
        http: aiohttp.ClientSession,
        url: str,
        auth_header: str | None,
        timeout_s: int,
    ) -> None:
        self._http = http
        self._url = url
        self._auth_header = auth_header
        self._timeout = timeout_s

    async def fetch(self, chat_id: int, chat_title: str, messages: Iterable[Message]) -> str:
        if not self._url:
            return ""

        payload = {
            "chat_id": chat_id,
            "chat_title": chat_title,
            "messages": [
                {
                    "sender": m.sender,
                    "timestamp": m.timestamp.isoformat(),
                    "text": m.text,
                }
                for m in messages
            ],
        }
        headers = {"Authorization": self._auth_header} if self._auth_header else {}

        try:
            async with self._http.post(
                self._url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                if resp.status >= 300:
                    log.warning("enrichment returned %s", resp.status)
                    return ""
                data = await resp.json()
                return str(data.get("context", ""))
        except asyncio.TimeoutError:
            log.warning("enrichment timed out after %ss", self._timeout)
            return ""
        except aiohttp.ClientError as e:
            log.warning("enrichment network error: %s", e)
            return ""
```

- [ ] **Step 5: Run tests, expect pass**

```bash
uv run pytest tests/unit/modules/drafting/test_enricher.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/telegram_assistant/modules/ tests/unit/modules/
git commit -m "feat(drafting): add best-effort enrichment HTTP client"
```

---

## Task 12: Drafting pipeline

Combines system prompt + enrichment + history + user instruction and calls the LLM. Pure orchestration — no Telegram I/O.

**Files:**
- Create: `src/telegram_assistant/modules/drafting/pipeline.py`
- Create: `tests/unit/modules/drafting/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

from telegram_assistant.modules.drafting.pipeline import build_user_prompt, Pipeline
from telegram_assistant.events import Message
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import make_message


def test_build_user_prompt_with_all_parts():
    history = [make_message(1, "alice", "hi"), make_message(1, "me", "hello", outgoing=True)]
    out = build_user_prompt(
        enrichment="calendar: meeting at 5",
        history=history,
        instruction="ask about tomorrow",
    )
    assert "External context:" in out
    assert "calendar: meeting at 5" in out
    assert "[alice" in out
    assert "[me" in out
    assert "Extra instruction:" in out
    assert "ask about tomorrow" in out
    assert "Draft a reply." in out


def test_build_user_prompt_omits_empty_sections():
    out = build_user_prompt(
        enrichment="",
        history=[make_message(1, "alice", "hi")],
        instruction="",
    )
    assert "External context:" not in out
    assert "Extra instruction:" not in out
    assert "[alice" in out


async def test_pipeline_calls_llm_with_composed_prompt():
    llm = fake_llm("DRAFT")
    pipe = Pipeline(llm=llm, system_prompt="SP")
    out = await pipe.run(
        enrichment="E",
        history=[make_message(1, "alice", "h")],
        instruction="I",
    )
    assert out == "DRAFT"
```

- [ ] **Step 2: Run, expect import error**

```bash
uv run pytest tests/unit/modules/drafting/test_pipeline.py -v
```

- [ ] **Step 3: Implement `src/telegram_assistant/modules/drafting/pipeline.py`**

```python
"""Draft-generation orchestration. Pure logic, no Telegram I/O."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from telegram_assistant.events import Message
from telegram_assistant.llm import LLMFactory


def build_user_prompt(enrichment: str, history: Sequence[Message], instruction: str) -> str:
    parts: list[str] = []
    if enrichment.strip():
        parts.append(f"External context:\n{enrichment.strip()}\n")
    parts.append("Conversation so far (most recent last):")
    for m in history:
        who = "me" if m.outgoing else m.sender
        parts.append(f"[{who} {m.timestamp.isoformat()}] {m.text}")
    if instruction.strip():
        parts.append(f"\nExtra instruction:\n{instruction.strip()}\n")
    parts.append("\nDraft a reply.")
    return "\n".join(parts)


@dataclass
class Pipeline:
    llm: LLMFactory
    system_prompt: str

    async def run(
        self, *, enrichment: str, history: Sequence[Message], instruction: str
    ) -> str:
        agent = self.llm.agent(self.system_prompt)
        prompt = build_user_prompt(enrichment, history, instruction)
        return await self.llm.run(agent, prompt)
```

- [ ] **Step 4: Run tests, expect pass**

```bash
uv run pytest tests/unit/modules/drafting/test_pipeline.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/modules/drafting/pipeline.py tests/unit/modules/drafting/test_pipeline.py
git commit -m "feat(drafting): add prompt-composing pipeline"
```

---

## Task 13: DraftingModule

Orchestrates markers, auto-draft policy, state management.

**Files:**
- Create: `src/telegram_assistant/modules/drafting/module.py`
- Create: `tests/unit/modules/drafting/test_module.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.events import DraftUpdate, IncomingMessage
from telegram_assistant.markers import MarkerMatch
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.drafting.module import DraftingModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


def _module_config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "enabled": True,
        "default_system_prompt": "SP",
        "last_n": 3,
        "auto_draft_chats": [],
        "enrichment_url": "",
        "enrichment_auth_header": "",
        "enrichment_timeout_s": 5,
        "markers": {"draft": "/draft", "auto_on": "/auto on", "auto_off": "/auto off"},
    }
    base.update(overrides)
    return base


async def _ctx(tmp_path: Path, config: dict[str, Any]) -> tuple[ModuleContext, FakeTelegramClient, RuntimeState]:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    ctx = ModuleContext(
        tg=tg,
        llm=fake_llm("GENERATED"),
        http=http,
        config=config,
        state=state.for_module("drafting"),
        log=logging.getLogger("drafting"),
    )
    return ctx, tg, state


async def test_markers_use_defaults():
    mod = DraftingModule()
    ctx, _, _ = await _ctx(Path("/tmp"), _module_config())
    await mod.init(ctx)
    names = {m.name for m in mod.markers()}
    triggers = {m.trigger for m in mod.markers()}
    assert names == {"draft", "auto_on", "auto_off"}
    assert triggers == {"/draft", "/auto on", "/auto off"}
    await ctx.http.close()


async def test_markers_respect_user_overrides(tmp_path: Path):
    mod = DraftingModule()
    ctx, _, _ = await _ctx(
        tmp_path,
        _module_config(markers={"draft": "!d", "auto_on": "!on", "auto_off": "!off"}),
    )
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"!d", "!on", "!off"}
    await ctx.http.close()


async def test_on_incoming_skips_outgoing(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="me", text="x", outgoing=True))
    )
    assert tg.drafts == {}
    await ctx.http.close()


async def test_on_incoming_auto_draft_whitelisted(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_on_incoming_skips_when_not_whitelisted(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config(auto_draft_chats=[]))
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts == {}
    await ctx.http.close()


async def test_runtime_state_overrides_seed_to_off(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    state.for_module("drafting").set("auto_draft", "1", False)
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts == {}
    await ctx.http.close()


async def test_runtime_state_overrides_seed_to_on(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[]))
    state.for_module("drafting").set("auto_draft", "1", True)
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(chat_id=1, sender="alice", text="hi"))
    )
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


async def test_auto_on_sets_state_and_confirms(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config())
    await mod.init(ctx)
    match = _find_marker(mod, "auto_on")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto on"), match)
    assert state.for_module("drafting").get("auto_draft", "1", default=None) is True
    assert tg.drafts[1].startswith("✓ Auto-draft enabled")
    await ctx.http.close()


async def test_auto_off_sets_state_and_confirms(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, state = await _ctx(tmp_path, _module_config(auto_draft_chats=[1]))
    await mod.init(ctx)
    match = _find_marker(mod, "auto_off")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/auto off"), match)
    assert state.for_module("drafting").get("auto_draft", "1", default=None) is False
    assert tg.drafts[1].startswith("✓ Auto-draft disabled")
    await ctx.http.close()


async def test_draft_marker_runs_pipeline(tmp_path: Path):
    mod = DraftingModule()
    ctx, tg, _ = await _ctx(tmp_path, _module_config())
    tg.seed_history(1, [make_message(1, "alice", "hi")])
    await mod.init(ctx)
    match = _find_marker(mod, "draft")
    # Simulate a resolved marker with a remainder
    from telegram_assistant.markers import MarkerMatch

    match_with_remainder = MarkerMatch(module="drafting", marker=match.marker, remainder="ask X")
    await mod.on_draft_update(DraftUpdate(chat_id=1, text="/draft ask X"), match_with_remainder)
    assert tg.drafts[1] == "GENERATED"
    await ctx.http.close()


def _find_marker(mod: DraftingModule, name: str) -> MarkerMatch:
    for m in mod.markers():
        if m.name == name:
            return MarkerMatch(module="drafting", marker=m, remainder="")
    raise AssertionError(f"no marker {name}")
```

- [ ] **Step 2: Run, expect import error**

```bash
uv run pytest tests/unit/modules/drafting/test_module.py -v
```

- [ ] **Step 3: Implement `src/telegram_assistant/modules/drafting/module.py`**

```python
"""Drafting module. Owns /draft, /auto on, /auto off markers and auto-draft policy."""
from __future__ import annotations

from typing import Any

from telegram_assistant.events import DraftUpdate, IncomingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext

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
        self._ctx.state.set("auto_draft", str(chat_id), on)
        word = "enabled" if on else "disabled"
        text = f"✓ Auto-draft {word} for this chat"
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
```

- [ ] **Step 4: Run tests, expect pass**

```bash
uv run pytest tests/unit/modules/drafting/test_module.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/modules/drafting/module.py tests/unit/modules/drafting/test_module.py
git commit -m "feat(drafting): add module with /draft and /auto on|off handling"
```

---

## Task 14: CorrectingModule

**Files:**
- Create: `src/telegram_assistant/modules/correcting/__init__.py`
- Create: `src/telegram_assistant/modules/correcting/module.py`
- Create: `tests/unit/modules/correcting/__init__.py`
- Create: `tests/unit/modules/correcting/test_module.py`

- [ ] **Step 1: Create empty init files**

```bash
mkdir -p src/telegram_assistant/modules/correcting tests/unit/modules/correcting
touch src/telegram_assistant/modules/correcting/__init__.py
touch tests/unit/modules/correcting/__init__.py
```

- [ ] **Step 2: Write failing tests**

```python
from __future__ import annotations

import logging
from pathlib import Path

import aiohttp

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.correcting.module import CorrectingModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def _ctx(tmp_path: Path, user_trigger: str | None = None) -> ModuleContext:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    markers = {"fix": user_trigger} if user_trigger else {}
    config = {
        "enabled": True,
        "system_prompt": "fix grammar",
        "markers": markers,
    }
    return ModuleContext(
        tg=tg,
        llm=fake_llm("CORRECTED"),
        http=http,
        config=config,
        state=state.for_module("correcting"),
        log=logging.getLogger("c"),
    )


async def test_default_marker(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path)
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"/fix"}
    await ctx.http.close()


async def test_custom_marker(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path, user_trigger="!fix")
    await mod.init(ctx)
    triggers = {m.trigger for m in mod.markers()}
    assert triggers == {"!fix"}
    await ctx.http.close()


async def test_fix_rewrites_remainder(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path)
    await mod.init(ctx)
    m = mod.markers()[0]
    match = MarkerMatch(module="correcting", marker=m, remainder="hi how ar you")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/fix hi how ar you"), match)
    assert ctx.tg.drafts[5] == "CORRECTED"  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_empty_remainder_ignored(tmp_path: Path):
    mod = CorrectingModule()
    ctx = await _ctx(tmp_path)
    await mod.init(ctx)
    m = mod.markers()[0]
    match = MarkerMatch(module="correcting", marker=m, remainder="")
    await mod.on_draft_update(DraftUpdate(chat_id=5, text="/fix"), match)
    assert ctx.tg.drafts == {}  # type: ignore[attr-defined]
    await ctx.http.close()
```

- [ ] **Step 3: Run, expect import error**

```bash
uv run pytest tests/unit/modules/correcting/test_module.py -v
```

- [ ] **Step 4: Implement `src/telegram_assistant/modules/correcting/module.py`**

```python
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
```

- [ ] **Step 5: Run tests, expect pass**

```bash
uv run pytest tests/unit/modules/correcting/test_module.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/telegram_assistant/modules/correcting/ tests/unit/modules/correcting/
git commit -m "feat(correcting): add /fix module"
```

---

## Task 15: Media reply backends

`DownloadBackend` protocol + `YtDlpBackend` implementation via subprocess.

**Files:**
- Create: `src/telegram_assistant/modules/media_reply/__init__.py`
- Create: `src/telegram_assistant/modules/media_reply/backends.py`
- Create: `tests/unit/modules/media_reply/__init__.py`
- Create: `tests/unit/modules/media_reply/test_backends.py`

- [ ] **Step 1: Create empty init files**

```bash
mkdir -p src/telegram_assistant/modules/media_reply tests/unit/modules/media_reply
touch src/telegram_assistant/modules/media_reply/__init__.py
touch tests/unit/modules/media_reply/__init__.py
```

- [ ] **Step 2: Write failing tests**

```python
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_assistant.modules.media_reply.backends import (
    DownloadError,
    YtDlpBackend,
    get_backend,
)


def test_get_backend_known():
    assert isinstance(get_backend("yt_dlp", timeout_s=10), YtDlpBackend)


def test_get_backend_unknown():
    with pytest.raises(KeyError):
        get_backend("nope", timeout_s=10)


async def test_yt_dlp_backend_success(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=10)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        # Simulate yt-dlp producing one output file.
        (tmp_path / "out.mp4").write_bytes(b"video")
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    result = await backend.download("https://example.com/x", tmp_path)
    assert result.name == "out.mp4"


async def test_yt_dlp_backend_nonzero_exit(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=10)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b"error"))
        proc.returncode = 1
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DownloadError):
        await backend.download("https://example.com/x", tmp_path)


async def test_yt_dlp_backend_timeout(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=0)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        async def slow():
            await asyncio.sleep(5)
            return (b"", b"")
        proc.communicate = AsyncMock(side_effect=slow)
        proc.kill = MagicMock()
        proc.returncode = None
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DownloadError):
        await backend.download("https://example.com/x", tmp_path)


async def test_yt_dlp_backend_no_output_file(tmp_path, monkeypatch):
    backend = YtDlpBackend(timeout_s=10)

    async def fake_exec(*cmd, stdout=None, stderr=None):
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 0
        # no files created
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(DownloadError):
        await backend.download("https://example.com/x", tmp_path)
```

- [ ] **Step 3: Run, expect import error**

```bash
uv run pytest tests/unit/modules/media_reply/test_backends.py -v
```

- [ ] **Step 4: Implement `src/telegram_assistant/modules/media_reply/backends.py`**

```python
"""Download backends for media_reply."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Protocol


class DownloadError(RuntimeError):
    pass


class DownloadBackend(Protocol):
    async def download(self, url: str, dest_dir: Path) -> Path: ...


class YtDlpBackend:
    def __init__(self, timeout_s: int) -> None:
        self._timeout = timeout_s

    async def download(self, url: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        before = set(dest_dir.iterdir())
        cmd = [
            "yt-dlp",
            "--no-playlist",
            "--restrict-filenames",
            "-o",
            str(dest_dir / "%(title).80B.%(ext)s"),
            url,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise DownloadError(f"yt-dlp timed out after {self._timeout}s") from None

        if proc.returncode != 0:
            raise DownloadError(
                f"yt-dlp exited {proc.returncode}: {stderr.decode(errors='replace')}"
            )

        after = set(dest_dir.iterdir())
        new = after - before
        if not new:
            raise DownloadError("yt-dlp produced no output file")
        # Return the newest file.
        return max(new, key=lambda p: p.stat().st_mtime)


_BACKENDS: dict[str, type] = {"yt_dlp": YtDlpBackend}


def get_backend(name: str, *, timeout_s: int) -> DownloadBackend:
    cls = _BACKENDS[name]
    return cls(timeout_s=timeout_s)
```

- [ ] **Step 5: Run tests, expect pass**

```bash
uv run pytest tests/unit/modules/media_reply/test_backends.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/telegram_assistant/modules/media_reply/ tests/unit/modules/media_reply/test_backends.py
git commit -m "feat(media_reply): add DownloadBackend protocol and YtDlpBackend"
```

---

## Task 16: MediaReplyModule

Matches URL regex, invokes backend, sends reply.

**Files:**
- Create: `src/telegram_assistant/modules/media_reply/module.py`
- Create: `tests/unit/modules/media_reply/test_module.py`

- [ ] **Step 1: Write failing tests**

```python
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from telegram_assistant.events import IncomingMessage
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.media_reply.backends import DownloadBackend, DownloadError
from telegram_assistant.modules.media_reply.module import MediaReplyModule
from telegram_assistant.state import RuntimeState
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


class FakeBackend:
    def __init__(self, path: Path | None = None, err: Exception | None = None) -> None:
        self._path = path
        self._err = err
        self.calls: list[str] = []

    async def download(self, url: str, dest_dir: Path) -> Path:
        self.calls.append(url)
        if self._err:
            raise self._err
        assert self._path is not None
        dest = dest_dir / self._path.name
        dest.write_bytes(b"x")
        return dest


async def _ctx(tmp_path: Path, config: dict[str, Any], backend: FakeBackend) -> ModuleContext:
    tg = FakeTelegramClient()
    state = RuntimeState(tmp_path / "state.toml")
    http = aiohttp.ClientSession()
    ctx = ModuleContext(
        tg=tg,
        llm=fake_llm(""),
        http=http,
        config=config,
        state=state.for_module("media_reply"),
        log=logging.getLogger("mr"),
    )
    # Inject the backend so tests are hermetic.
    MediaReplyModule._backend_override = backend  # type: ignore[attr-defined]
    return ctx


def _instagram_cfg(chats: list[int]) -> dict[str, Any]:
    return {
        "enabled": True,
        "chats": chats,
        "send_as": "reply",
        "download_timeout_s": 5,
        "handlers": [
            {
                "name": "instagram",
                "pattern": r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[\w-]+",
                "backend": "yt_dlp",
            }
        ],
    }


async def test_whitelisted_match_triggers_download(tmp_path: Path):
    backend = FakeBackend(path=Path("video.mp4"))
    ctx = await _ctx(tmp_path, _instagram_cfg([42]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(
            make_message(42, "alice", "look https://instagram.com/reel/abc123", message_id=9)
        )
    )
    assert backend.calls == ["https://instagram.com/reel/abc123"]
    assert len(ctx.tg.sent) == 1  # type: ignore[attr-defined]
    sent = ctx.tg.sent[0]  # type: ignore[attr-defined]
    assert sent.chat_id == 42
    assert sent.reply_to == 9
    await ctx.http.close()


async def test_not_whitelisted_no_action(tmp_path: Path):
    backend = FakeBackend(path=Path("video.mp4"))
    ctx = await _ctx(tmp_path, _instagram_cfg([1]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(42, "alice", "https://instagram.com/reel/abc"))
    )
    assert backend.calls == []
    assert ctx.tg.sent == []  # type: ignore[attr-defined]
    await ctx.http.close()


async def test_no_match_no_action(tmp_path: Path):
    backend = FakeBackend(path=Path("video.mp4"))
    ctx = await _ctx(tmp_path, _instagram_cfg([1]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(IncomingMessage(make_message(1, "alice", "no link here")))
    assert backend.calls == []
    await ctx.http.close()


async def test_backend_failure_logged_no_reply(tmp_path: Path, caplog):
    backend = FakeBackend(err=DownloadError("boom"))
    ctx = await _ctx(tmp_path, _instagram_cfg([1]), backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    await mod.on_incoming_message(
        IncomingMessage(make_message(1, "alice", "https://instagram.com/p/xyz"))
    )
    assert ctx.tg.sent == []  # type: ignore[attr-defined]
    assert any("boom" in r.message for r in caplog.records)
    await ctx.http.close()


async def test_first_handler_wins(tmp_path: Path):
    backend = FakeBackend(path=Path("clip.mp4"))
    cfg = _instagram_cfg([1])
    cfg["handlers"].append(
        {"name": "tiktok", "pattern": r"tiktok\.com", "backend": "yt_dlp"}
    )
    ctx = await _ctx(tmp_path, cfg, backend)
    mod = MediaReplyModule()
    await mod.init(ctx)
    # Message has both an instagram and a tiktok link; instagram is first in config.
    await mod.on_incoming_message(
        IncomingMessage(
            make_message(
                1, "alice", "x https://instagram.com/p/1 y https://tiktok.com/@u/video/1"
            )
        )
    )
    assert backend.calls == ["https://instagram.com/p/1"]
    await ctx.http.close()
```

- [ ] **Step 2: Run, expect import error**

```bash
uv run pytest tests/unit/modules/media_reply/test_module.py -v
```

- [ ] **Step 3: Implement `src/telegram_assistant/modules/media_reply/module.py`**

```python
"""Media-reply module. Matches URL regexes in incoming messages, downloads, replies."""
from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram_assistant.events import IncomingMessage
from telegram_assistant.module import ModuleContext

from .backends import DownloadBackend, DownloadError, get_backend


@dataclass
class Handler:
    name: str
    pattern: re.Pattern[str]
    backend: DownloadBackend


class MediaReplyModule:
    name = "media_reply"

    _backend_override: DownloadBackend | None = None  # test hook

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._handlers: list[Handler] = []
        self._chats: set[int] = set()

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        self._chats = {int(c) for c in cfg.get("chats", [])}
        timeout_s = int(cfg.get("download_timeout_s", 60))
        self._handlers = []
        for h in cfg.get("handlers", []):
            backend: DownloadBackend = (
                MediaReplyModule._backend_override
                if MediaReplyModule._backend_override is not None
                else get_backend(h["backend"], timeout_s=timeout_s)
            )
            self._handlers.append(
                Handler(
                    name=h["name"],
                    pattern=re.compile(h["pattern"]),
                    backend=backend,
                )
            )

    async def shutdown(self) -> None:
        return

    def markers(self):
        return []

    async def on_incoming_message(self, event: IncomingMessage) -> None:
        assert self._ctx is not None
        msg = event.message
        if msg.chat_id not in self._chats:
            return
        match_url: str | None = None
        picked: Handler | None = None
        for h in self._handlers:
            m = h.pattern.search(msg.text)
            if m:
                match_url = m.group(0)
                picked = h
                break
        if picked is None or match_url is None:
            return

        with tempfile.TemporaryDirectory(prefix="tga-media-") as td:
            td_path = Path(td)
            try:
                file_path = await picked.backend.download(match_url, td_path)
            except DownloadError as e:
                self._ctx.log.warning("download failed (%s): %s", picked.name, e)
                return
            await self._ctx.tg.send_message(
                chat_id=msg.chat_id,
                reply_to=msg.message_id,
                files=[file_path],
            )
```

- [ ] **Step 4: Run tests, expect pass**

```bash
uv run pytest tests/unit/modules/media_reply/test_module.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/modules/media_reply/module.py tests/unit/modules/media_reply/test_module.py
git commit -m "feat(media_reply): add regex-matching reply module"
```

---

## Task 17: ModuleLoader

Reads `[modules.*]` from config, instantiates known modules, registers markers.

**Files:**
- Create: `src/telegram_assistant/module_loader.py`
- Create: `tests/unit/test_module_loader.py`

- [ ] **Step 1: Write failing tests**

```python
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
```

- [ ] **Step 2: Run, expect import error**

```bash
uv run pytest tests/unit/test_module_loader.py -v
```

- [ ] **Step 3: Implement `src/telegram_assistant/module_loader.py`**

```python
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
```

- [ ] **Step 4: Run tests, expect pass**

```bash
uv run pytest tests/unit/test_module_loader.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/module_loader.py tests/unit/test_module_loader.py
git commit -m "feat(core): add module loader with marker registration"
```

---

## Task 18: Application wiring

`App` glues everything: receives events from the Telegram client, dispatches via the bus (draft updates routed via marker registry, incoming messages broadcast to all modules).

**Files:**
- Create: `src/telegram_assistant/app.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/integration/test_drafting_flow.py`
- Create: `tests/integration/test_correcting_flow.py`
- Create: `tests/integration/test_auto_toggle_flow.py`
- Create: `tests/integration/test_media_reply_flow.py`

- [ ] **Step 1: Write integration test for drafting auto-flow**

`tests/integration/test_drafting_flow.py`:

```python
from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import IncomingMessage
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


async def test_auto_draft_end_to_end(tmp_path: Path):
    tg = FakeTelegramClient()
    tg.seed_history(42, [make_message(42, "alice", "hi")])
    http = aiohttp.ClientSession()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [42],
            "enrichment_url": "",
            "markers": {},
        }
    }
    app = App(tg=tg, llm=fake_llm("HELLO BACK"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_incoming(IncomingMessage(make_message(42, "alice", "hi")))
    await app.drain()
    assert tg.drafts[42] == "HELLO BACK"
    await app.stop()
    await http.close()
```

- [ ] **Step 2: Write integration test for /fix**

`tests/integration/test_correcting_flow.py`:

```python
from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient


async def test_fix_marker_end_to_end(tmp_path: Path):
    tg = FakeTelegramClient()
    http = aiohttp.ClientSession()
    modules_cfg = {"correcting": {"enabled": True, "system_prompt": "fix", "markers": {}}}
    app = App(tg=tg, llm=fake_llm("CORRECTED"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_draft_update(DraftUpdate(chat_id=7, text="/fix hi how ar you"))
    await app.drain()
    assert tg.drafts[7] == "CORRECTED"
    await app.stop()
    await http.close()
```

- [ ] **Step 3: Write integration test for /auto on / off**

`tests/integration/test_auto_toggle_flow.py`:

```python
from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate, IncomingMessage
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


async def test_auto_on_then_incoming_drafts(tmp_path: Path):
    tg = FakeTelegramClient()
    tg.seed_history(9, [make_message(9, "alice", "hi")])
    http = aiohttp.ClientSession()
    modules_cfg = {
        "drafting": {
            "enabled": True,
            "default_system_prompt": "SP",
            "last_n": 5,
            "auto_draft_chats": [],
            "enrichment_url": "",
            "markers": {},
        }
    }
    app = App(tg=tg, llm=fake_llm("R"), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)

    # First: /auto on flips state, writes confirmation.
    await app.inject_draft_update(DraftUpdate(chat_id=9, text="/auto on"))
    await app.drain()
    assert tg.drafts[9].startswith("✓ Auto-draft enabled")

    # Then: an incoming message is auto-drafted.
    await app.inject_incoming(IncomingMessage(make_message(9, "alice", "hi")))
    await app.drain()
    assert tg.drafts[9] == "R"

    # Finally: /auto off disables.
    await app.inject_draft_update(DraftUpdate(chat_id=9, text="/auto off"))
    await app.drain()
    assert tg.drafts[9].startswith("✓ Auto-draft disabled")

    await app.stop()
    await http.close()
```

- [ ] **Step 4: Write integration test for media_reply**

`tests/integration/test_media_reply_flow.py`:

```python
from __future__ import annotations

from pathlib import Path

import aiohttp

from telegram_assistant.app import App
from telegram_assistant.events import IncomingMessage
from telegram_assistant.modules.media_reply.module import MediaReplyModule
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


class StubBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def download(self, url: str, dest_dir: Path) -> Path:
        self.calls.append(url)
        p = dest_dir / "clip.mp4"
        p.write_bytes(b"video")
        return p


async def test_media_reply_end_to_end(tmp_path: Path):
    backend = StubBackend()
    MediaReplyModule._backend_override = backend  # type: ignore[attr-defined]
    tg = FakeTelegramClient()
    http = aiohttp.ClientSession()
    modules_cfg = {
        "media_reply": {
            "enabled": True,
            "chats": [5],
            "send_as": "reply",
            "download_timeout_s": 5,
            "handlers": [
                {
                    "name": "instagram",
                    "pattern": r"https?://instagram\.com/\S+",
                    "backend": "yt_dlp",
                }
            ],
        }
    }
    app = App(tg=tg, llm=fake_llm(""), http=http, state_path=tmp_path / "state.toml")
    await app.start(modules_cfg)
    await app.inject_incoming(
        IncomingMessage(make_message(5, "alice", "look https://instagram.com/reel/xyz", message_id=11))
    )
    await app.drain()
    assert backend.calls == ["https://instagram.com/reel/xyz"]
    assert len(tg.sent) == 1
    assert tg.sent[0].reply_to == 11
    await app.stop()
    await http.close()
    MediaReplyModule._backend_override = None  # type: ignore[attr-defined]
```

- [ ] **Step 5: Run all four — expect import errors**

```bash
uv run pytest tests/integration/ -v
```

- [ ] **Step 6: Implement `src/telegram_assistant/app.py`**

```python
"""Application glue.

Routing:
- DraftUpdate → marker_registry resolves winning module → on_draft_update.
- IncomingMessage → broadcast to all modules implementing on_incoming_message.
"""
from __future__ import annotations

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

    async def stop(self) -> None:
        for m in self._modules:
            await m.shutdown()

    async def inject_incoming(self, event: IncomingMessage) -> None:
        for m in self._modules:
            await self._bus.dispatch(
                "incoming", m.name, chat_id=event.message.chat_id, payload=event
            )

    async def inject_draft_update(self, event: DraftUpdate) -> None:
        if self._loop_protect.is_our_write(event.chat_id, event.text):
            return
        match = self._registry.resolve(event.text)
        if match is None:
            return
        module = next((m for m in self._modules if m.name == match.module), None)
        if module is None:
            return
        await self._bus.dispatch(
            "draft", module.name, chat_id=event.chat_id,
            payload=(event, match),
        )
        # on_draft_update is invoked via a handler registered on first use.
        if ("draft", module.name) not in getattr(self._bus, "_subs", {}):
            async def handler(payload):
                ev, mt = payload
                await module.on_draft_update(ev, mt)
            self._bus.subscribe("draft", module.name, handler)

    async def drain(self) -> None:
        await self._bus.drain()


class _LoopProtectingClient:
    """Wraps a TelegramClient to record our own draft writes for loop protection."""

    def __init__(self, inner: TelegramClient, lp: LoopProtection) -> None:
        self._inner = inner
        self._lp = lp

    async def write_draft(self, chat_id: int, text: str) -> None:
        self._lp.record(chat_id, text)
        await self._inner.write_draft(chat_id, text)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)
```

Note the draft-dispatch quirk: the handler for `("draft", module.name)` is registered lazily inside `inject_draft_update` so the bus's `_subs` carries the wrapper closure. Acceptable for initial implementation; consider refactoring to register handlers eagerly in `start()`. Update the implementation to register eagerly:

Replace the body of `start()` to register draft handlers up-front:

```python
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
```

And simplify `inject_draft_update`:

```python
    async def inject_draft_update(self, event: DraftUpdate) -> None:
        if self._loop_protect.is_our_write(event.chat_id, event.text):
            return
        match = self._registry.resolve(event.text)
        if match is None:
            return
        await self._bus.dispatch(
            "draft", match.module, chat_id=event.chat_id, payload=(event, match),
        )
```

- [ ] **Step 7: Run tests, expect pass**

```bash
uv run pytest tests/integration/ -v
```

- [ ] **Step 8: Run full suite, verify green**

```bash
uv run pytest -v
```

- [ ] **Step 9: Commit**

```bash
git add src/telegram_assistant/app.py tests/integration/
git commit -m "feat(app): add application wiring with loop-protecting client and integration tests"
```

---

## Task 19: Telethon concrete client

Wires the real MTProto client. Tests are light because this is the I/O boundary — correctness is verified via integration tests against the fake, plus a manual smoke run.

**Files:**
- Create: `src/telegram_assistant/telethon_client.py`

- [ ] **Step 1: Write `src/telegram_assistant/telethon_client.py`**

```python
"""Concrete Telethon-backed TelegramClient.

This is a thin adapter: translate Telethon events to our Message / IncomingMessage /
DraftUpdate types, and map our API methods onto Telethon calls.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timezone
from pathlib import Path
from typing import Awaitable, Callable

from telethon import TelegramClient as _Telethon
from telethon import events as _events
from telethon.tl.functions.messages import SaveDraftRequest
from telethon.tl.types import InputPeerEmpty

from .events import DraftUpdate, IncomingMessage, Message

log = logging.getLogger(__name__)

OnIncoming = Callable[[IncomingMessage], Awaitable[None]]
OnDraft = Callable[[DraftUpdate], Awaitable[None]]


class TelethonTelegramClient:
    def __init__(
        self,
        *,
        api_id: int,
        api_hash: str,
        session: str,
        on_incoming: OnIncoming,
        on_draft: OnDraft,
    ) -> None:
        self._client = _Telethon(session, api_id, api_hash)
        self._on_incoming = on_incoming
        self._on_draft = on_draft

    async def connect(self) -> None:
        await self._client.start()  # interactive login on first run

        @self._client.on(_events.NewMessage(incoming=True))
        async def _(event):
            msg = await self._to_message(event)
            await self._on_incoming(IncomingMessage(msg))

        @self._client.on(_events.Raw())
        async def _raw(update):
            # Detect UpdateDraftMessage.
            if type(update).__name__ == "UpdateDraftMessage":
                try:
                    chat_id = self._peer_to_chat_id(update.peer)
                    text = getattr(update.draft, "message", "") or ""
                    await self._on_draft(DraftUpdate(chat_id=chat_id, text=text))
                except Exception as e:
                    log.warning("failed to translate draft update: %s", e)

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def send_message(
        self,
        chat_id: int,
        text: str | None = None,
        reply_to: int | None = None,
        files: list[Path] | None = None,
    ) -> None:
        if files:
            await self._client.send_file(
                chat_id, file=[str(p) for p in files], caption=text or None, reply_to=reply_to
            )
        elif text is not None:
            await self._client.send_message(chat_id, text, reply_to=reply_to)

    async def write_draft(self, chat_id: int, text: str) -> None:
        peer = await self._client.get_input_entity(chat_id)
        await self._client(SaveDraftRequest(peer=peer, message=text))

    async def fetch_history(self, chat_id: int, n: int) -> list[Message]:
        out: list[Message] = []
        async for m in self._client.iter_messages(chat_id, limit=n):
            out.append(
                Message(
                    chat_id=chat_id,
                    message_id=m.id,
                    sender=str(getattr(m.sender, "username", None) or m.sender_id or "unknown"),
                    timestamp=m.date.astimezone(timezone.utc),
                    text=m.message or "",
                    outgoing=bool(m.out),
                )
            )
        out.reverse()
        return out

    async def download_media(self, message_id: int, chat_id: int, dest_dir: Path) -> Path:
        messages = await self._client.get_messages(chat_id, ids=message_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = await self._client.download_media(messages, file=str(dest_dir) + "/")
        return Path(path)

    async def _to_message(self, event) -> Message:
        sender = await event.get_sender()
        return Message(
            chat_id=event.chat_id,
            message_id=event.message.id,
            sender=str(getattr(sender, "username", None) or event.sender_id or "unknown"),
            timestamp=event.message.date.astimezone(timezone.utc),
            text=event.message.message or "",
            outgoing=bool(event.message.out),
        )

    @staticmethod
    def _peer_to_chat_id(peer) -> int:
        # Telethon peers come in user/chat/channel variants; fall back to int(str) if unknown.
        for attr in ("user_id", "chat_id", "channel_id"):
            v = getattr(peer, attr, None)
            if v is not None:
                return int(v) if attr == "user_id" else -int(v)
        raise ValueError(f"cannot extract chat id from peer {peer!r}")
```

- [ ] **Step 2: Smoke-check that it imports**

```bash
uv run python -c "from telegram_assistant.telethon_client import TelethonTelegramClient; print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add src/telegram_assistant/telethon_client.py
git commit -m "feat(telethon): add concrete TelegramClient adapter"
```

---

## Task 20: Entrypoint and hot-reload

CLI entrypoint + config file watching.

**Files:**
- Create: `src/telegram_assistant/__main__.py`
- Modify: `src/telegram_assistant/app.py` (add `run` that drives the real client)

- [ ] **Step 1: Extend `App` with a `run()` method**

Append to `src/telegram_assistant/app.py`:

```python
    async def run(self, modules_cfg: dict[str, dict[str, Any]]) -> None:
        """Drive the bus from external client events until cancelled.

        When used with a real TelethonTelegramClient, `tg` is expected to
        have been constructed with on_incoming/on_draft pointing at
        self.inject_incoming / self.inject_draft_update.
        """
        await self.start(modules_cfg)
        # Connect the client (interactive login on first run).
        await self._tg.connect()
        try:
            # Sleep forever; incoming events arrive via callbacks.
            await asyncio.Event().wait()
        finally:
            await self._tg.disconnect()
            await self.stop()
```

Also add `import asyncio` at the top of `app.py` if not already present.

- [ ] **Step 2: Write `src/telegram_assistant/__main__.py`**

```python
"""CLI entrypoint."""
from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

import aiohttp
from watchfiles import awatch

from .app import App
from .config import load_config
from .llm import LLMFactory
from .telethon_client import TelethonTelegramClient


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="telegram-assistant")
    p.add_argument("--config", type=Path, default=Path("config.toml"))
    p.add_argument("--state", type=Path, default=Path("state.toml"))
    p.add_argument("--log-level", default="INFO")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    logging.basicConfig(level=args.log_level.upper())
    cfg = load_config(args.config)
    http = aiohttp.ClientSession()

    # Construct App placeholder first so we can pass callbacks to the client.
    app_holder: dict[str, App] = {}

    async def on_incoming(event):
        await app_holder["app"].inject_incoming(event)

    async def on_draft(event):
        await app_holder["app"].inject_draft_update(event)

    tg = TelethonTelegramClient(
        api_id=cfg.telegram.api_id,
        api_hash=cfg.telegram.api_hash,
        session=cfg.telegram.session,
        on_incoming=on_incoming,
        on_draft=on_draft,
    )
    from pydantic_ai.models import KnownModelName  # noqa: F401 — sanity import
    from pydantic_ai import models as _pa_models

    # Build a pydantic-ai model from cfg.llm.model (string shorthand supported by pydantic-ai).
    llm = LLMFactory(model=cfg.llm.model, timeout_s=cfg.llm.timeout_s)

    app = App(tg=tg, llm=llm, http=http, state_path=args.state)
    app_holder["app"] = app

    async def watch_config() -> None:
        async for _changes in awatch(str(args.config)):
            try:
                new_cfg = load_config(args.config)
            except Exception as e:
                logging.warning("config reload failed: %s", e)
                continue
            logging.info("config reloaded (modules may require restart to apply)")
            # Simple strategy for now: log the change but do not hot-swap.
            # A follow-up can add per-module reconfigure() wiring.
            _ = new_cfg

    try:
        async with asyncio.TaskGroup() as tg_group:
            tg_group.create_task(app.run(cfg.modules))
            tg_group.create_task(watch_config())
    finally:
        await http.close()


def main() -> None:
    args = _parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify full suite still passes**

```bash
uv run pytest -v
```

- [ ] **Step 4: Verify CLI smokes (no connection expected)**

```bash
uv run python -m telegram_assistant --help
```

Expected: argparse help text.

- [ ] **Step 5: Commit**

```bash
git add src/telegram_assistant/__main__.py src/telegram_assistant/app.py
git commit -m "feat(cli): add entrypoint and config watcher scaffold"
```

---

## Task 21: Final cleanup and manual smoke-run checklist

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write `README.md`** (brief operator notes)

```markdown
# telegram-assistant

Modular Telegram userbot. See `docs/superpowers/specs/2026-04-19-telegram-assistant-design.md` for design and `docs/superpowers/plans/2026-04-19-telegram-assistant.md` for the implementation plan.

## First run

1. Copy `config.example.toml` to `config.toml` and fill in:
   - `[telegram]` — `api_id`, `api_hash` from https://my.telegram.org
   - `[llm]` — a model slug supported by `pydantic-ai` (e.g. `anthropic:claude-sonnet-4-6`); set the matching API key in your environment.
2. `uv sync`
3. `uv run telegram-assistant --config config.toml --state state.toml`
   - On first run Telethon will prompt for your phone number and a login code.
4. The second run reuses the `*.session` file.

## Tests

```bash
uv run pytest
```

## Manual smoke flow

1. In your Telegram client, open any chat and type `/draft`. Pause briefly.
2. The input field should update to a generated draft.
3. Add a chat to auto-draft with `/auto on` (in that chat's input).
4. Type `/fix hi how ar you`. The input field should update to the corrected text.
5. Paste an Instagram URL into a chat listed under `modules.media_reply.chats`. The bot should reply with the downloaded media.
```

- [ ] **Step 2: Full suite green**

```bash
uv run pytest -v
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: add operator README"
```

---

## Self-review (author checklist)

**Spec coverage:**
- §3 Architecture — covered by Tasks 5–10, 17, 18.
- §4.1 Drafting (markers, auto-draft, /auto, enrichment, resolution) — Tasks 11–13, 18.
- §4.2 Correcting — Task 14.
- §4.3 Media reply — Tasks 15–16, 18.
- §5 Config + state — Tasks 1, 2, 3, 17.
- §6 LLM — Task 9.
- §7 Error handling — assertions and tests in Tasks 11, 12, 13, 14, 15, 16.
- §8 Testing — all tasks TDD; integration tests in Task 18.
- §9 Stack — Task 1 (pyproject).
- §10 Tradeoffs — documented; §11 open items deferred (intentional).
- §5.4 Hot reload — Task 20 scaffolds the watcher; full `reconfigure()` wiring deferred (noted in task).

**Placeholder scan:** no "TBD" / "TODO" / "handle edge cases" left in steps.

**Type consistency:**
- `LLMFactory.agent()` returns a Pydantic AI `Agent[None, str]` (Task 9); consumers call `llm.run(agent, text)` everywhere.
- `TelegramClient` protocol (Task 10) matches usage by modules and `_LoopProtectingClient`.
- `MarkerMatch(module, marker, remainder)` shape matches producer (`MarkerRegistry.resolve` in Task 6) and consumers (Task 13, 14, 18).
- `Message` dataclass shape consistent across fake, real client, enricher, pipeline.

**Known deferrals documented in the plan:**
- Telethon E2E test — smoke via manual run (Task 21 checklist).
- Hot-reload `reconfigure()` full wiring — Task 20 logs changes; per-module reconfigure left as follow-up.
- `yt-dlp` cookies/auth for Instagram — configurable via yt-dlp's own mechanisms; no extra code needed in v1.
