"""Per-chat feature toggles that modules expose to cross-module UIs.

A module that owns per-chat on/off switches (``/auto_fix on`` etc.) can
implement ``toggles() -> list[Toggle]``. Each ``Toggle`` is bound to the
module's own effective-value logic (state override, else config seed) and
writes through the module's ``ModuleState`` — so the app's
``RuntimeState`` stays the single writer of ``state.toml`` and the owning
module sees a change on its very next read.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Iterable


@dataclass(frozen=True)
class Toggle:
    module: str
    bucket: str
    label: str
    # Effective value for a chat: state.toml override, else config seed.
    is_on: Callable[[int], bool]
    # Persist an override. Raises StateWriteError if the write failed.
    set: Callable[[int, bool], None]
    # Every chat with an override or seed entry (on or off).
    chats: Callable[[], set[int]]

    @property
    def key(self) -> str:
        return f"{self.module}.{self.bucket}"


def chat_ids(keys: Iterable[str]) -> set[int]:
    """state.toml keys (stringified chat ids) → ints; junk keys are skipped."""
    out: set[int] = set()
    for k in keys:
        try:
            out.add(int(k))
        except ValueError:
            continue
    return out
