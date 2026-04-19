from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from telegram_assistant.state import RuntimeState, StateWriteError


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


def test_write_failure_raises_state_write_error_and_keeps_memory(tmp_path: Path):
    state = RuntimeState(tmp_path / "state.toml")
    ns = state.for_module("drafting")
    with patch("os.replace", side_effect=OSError("disk full")):
        with pytest.raises(StateWriteError):
            ns.set("auto_draft", "99", True)
    # In-memory change is preserved despite the write failure.
    assert ns.get("auto_draft", "99", default=None) is True
