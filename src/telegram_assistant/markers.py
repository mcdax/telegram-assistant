"""Marker definitions and registry.

A Marker is a trigger string a module registers. On a DraftUpdate,
the registry selects at most one winning marker (highest priority, ties
broken by registration order) and the matching module handles the event.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union


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

    def resolve(self, text: str) -> Union[MarkerMatch, None]:
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
