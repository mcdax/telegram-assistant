from __future__ import annotations

import pytest

from telegram_assistant.config import Config, ConfigError, load_config


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep host-level overrides from affecting these tests."""
    for name in ("LLM_MODEL", "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_SESSION"):
        monkeypatch.delenv(name, raising=False)


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


def test_llm_model_from_env(write_toml, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "env:picked")
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"

        [llm]
        timeout_s = 10
        """
    )
    cfg = load_config(path)
    assert cfg.llm.model == "env:picked"


def test_llm_model_env_overrides_config(write_toml, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "env:picked")
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"

        [llm]
        model = "config:value"
        timeout_s = 10
        """
    )
    cfg = load_config(path)
    assert cfg.llm.model == "env:picked"


def test_llm_model_missing_everywhere_raises(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"

        [llm]
        timeout_s = 10
        """
    )
    with pytest.raises(ConfigError, match="LLM_MODEL"):
        load_config(path)


def test_llm_timeout_still_required(write_toml, monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "env:picked")
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "h"
        session = "s"

        [llm]
        """
    )
    with pytest.raises(ConfigError, match="timeout_s"):
        load_config(path)


def test_telegram_credentials_from_env(write_toml, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "999")
    monkeypatch.setenv("TELEGRAM_API_HASH", "hash-from-env")
    monkeypatch.setenv("TELEGRAM_SESSION", "session-from-env")
    path = write_toml(
        """
        [telegram]

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    cfg = load_config(path)
    assert cfg.telegram.api_id == 999
    assert cfg.telegram.api_hash == "hash-from-env"
    assert cfg.telegram.session == "session-from-env"


def test_telegram_env_overrides_config(write_toml, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "999")
    monkeypatch.setenv("TELEGRAM_API_HASH", "env-hash")
    monkeypatch.setenv("TELEGRAM_SESSION", "env-session")
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "cfg-hash"
        session = "cfg-session"

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    cfg = load_config(path)
    assert cfg.telegram.api_id == 999
    assert cfg.telegram.api_hash == "env-hash"
    assert cfg.telegram.session == "env-session"


def test_telegram_placeholder_api_hash_rejected(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 1
        api_hash = "YOUR_API_HASH"
        session = "s"

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    with pytest.raises(ConfigError, match="api_hash"):
        load_config(path)


def test_telegram_api_id_zero_rejected(write_toml):
    path = write_toml(
        """
        [telegram]
        api_id = 0
        api_hash = "h"
        session = "s"

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    with pytest.raises(ConfigError, match="api_id"):
        load_config(path)


def test_telegram_api_id_non_integer_env_raises(write_toml, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "not-a-number")
    path = write_toml(
        """
        [telegram]

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    with pytest.raises(ConfigError, match="api_id must be an integer"):
        load_config(path)


def test_telegram_missing_everywhere_raises(write_toml):
    path = write_toml(
        """
        [telegram]

        [llm]
        model = "m"
        timeout_s = 10
        """
    )
    with pytest.raises(ConfigError, match="TELEGRAM_"):
        load_config(path)
