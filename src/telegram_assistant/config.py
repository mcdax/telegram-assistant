"""Config loader. Reads and validates config.toml into typed dataclasses."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODEL_ENV_VAR = "LLM_MODEL"
TELEGRAM_API_ID_ENV_VAR = "TELEGRAM_API_ID"
TELEGRAM_API_HASH_ENV_VAR = "TELEGRAM_API_HASH"
TELEGRAM_SESSION_ENV_VAR = "TELEGRAM_SESSION"


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

    api_id_raw = os.environ.get(TELEGRAM_API_ID_ENV_VAR) or t.get("api_id")
    if api_id_raw in (None, "", 0):
        raise ConfigError(
            f"missing telegram.api_id (set in config or via {TELEGRAM_API_ID_ENV_VAR} env var)"
        )
    try:
        api_id = int(api_id_raw)
    except (TypeError, ValueError) as e:
        raise ConfigError(f"telegram.api_id must be an integer, got {api_id_raw!r}") from e

    api_hash = os.environ.get(TELEGRAM_API_HASH_ENV_VAR) or t.get("api_hash")
    if not api_hash or api_hash == "YOUR_API_HASH":
        raise ConfigError(
            f"missing telegram.api_hash (set in config or via {TELEGRAM_API_HASH_ENV_VAR} env var)"
        )

    session = os.environ.get(TELEGRAM_SESSION_ENV_VAR) or t.get("session")
    if not session:
        raise ConfigError(
            f"missing telegram.session (set in config or via {TELEGRAM_SESSION_ENV_VAR} env var)"
        )

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
            api_id=api_id,
            api_hash=str(api_hash),
            session=str(session),
        ),
        llm=LLMConfig(
            model=str(model),
            timeout_s=int(l["timeout_s"]),
        ),
        modules=dict(modules),
    )
