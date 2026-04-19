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
