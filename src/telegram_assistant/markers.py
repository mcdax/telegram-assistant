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
