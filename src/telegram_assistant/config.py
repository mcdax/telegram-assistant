"""Config loader. Reads and validates config.toml into typed dataclasses."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODEL_ENV_VAR = "LLM_MODEL"


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
    if "timeout_s" not in l:
        raise ConfigError("missing llm.timeout_s")

    model = os.environ.get(MODEL_ENV_VAR) or l.get("model")
    if not model:
        raise ConfigError(
            f"missing llm.model (set in config or via {MODEL_ENV_VAR} env var)"
        )

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
            model=str(model),
            timeout_s=int(l["timeout_s"]),
        ),
        modules=dict(modules),
    )
