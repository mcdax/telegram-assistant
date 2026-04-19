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
