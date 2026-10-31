"""Pure builders for the settings menu: texts, keyboards, callback data.

Kept free of I/O so the UX can be unit-tested without a fake Bot API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from telegram_assistant.toggles import Toggle

BTN_PICK_USER = "👤 Person wählen"
BTN_PICK_GROUP = "👥 Gruppe wählen"
BTN_OVERVIEW = "📋 Übersicht"

# Texts the reply keyboard can put into the bot chat. Other modules that
# read the bot chat via MTProto (agent clarifications) must ignore these.
MENU_BUTTON_TEXTS = frozenset({BTN_PICK_USER, BTN_PICK_GROUP, BTN_OVERVIEW})

PICKER_PROMPT = "Für welchen Chat willst du etwas einstellen?"
NO_FEATURES = "Keine einstellbaren Funktionen aktiv."
NOTHING_ON = "📋 Noch nichts eingeschaltet."

_REQ_USER = 1
_REQ_GROUP = 2

# Display order of known toggles; unknown ones follow in registry order.
_ORDER = (
    "correcting.auto_fix",
    "correcting.auto_fix_sent",
    "drafting.auto_draft",
    "media_reply.auto_media",
)

# Overview lines/buttons beyond this are summarised (inline keyboards cap
# at 100 buttons, messages at 4096 chars).
OVERVIEW_MAX = 40


def bot_api_chat_id(bot_chat_id: int) -> int:
    """Bot API chat id → the chat_id our modules use as state key.

    Telethon's ``event.chat_id`` / ``get_peer_id`` produce *marked* ids:
    users ``id``, basic groups ``-id``, channels/supergroups ``-100…id``.
    That is exactly the Bot API's encoding, so the mapping is the identity
    — kept as a named function so the contract is documented and tested.
    """
    return int(bot_chat_id)


def sort_toggles(toggles: list[Toggle]) -> list[Toggle]:
    def rank(t: Toggle) -> int:
        return _ORDER.index(t.key) if t.key in _ORDER else len(_ORDER)
    return sorted(toggles, key=rank)  # stable → unknowns keep registry order


def picker_keyboard() -> dict[str, Any]:
    return {
        "keyboard": [
            [{
                "text": BTN_PICK_USER,
                "request_users": {
                    "request_id": _REQ_USER,
                    "user_is_bot": False,
                    "max_quantity": 1,
                    "request_name": True,
                },
            }],
            [{
                "text": BTN_PICK_GROUP,
                "request_chat": {
                    "request_id": _REQ_GROUP,
                    "chat_is_channel": False,
                    "request_title": True,
                },
            }],
            [{"text": BTN_OVERVIEW}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def menu_text(name: str) -> str:
    return f"⚙️ Einstellungen für: {name}"


def toggle_keyboard(chat_id: int, toggles: list[Toggle]) -> dict[str, Any]:
    rows = [
        [{
            "text": f"{'✅' if t.is_on(chat_id) else '❌'} {t.label}",
            "callback_data": toggle_data(chat_id, t.key),
        }]
        for t in toggles
    ]
    return {"inline_keyboard": rows}


def toast(label: str, on: bool) -> str:
    return f"{label}: {'an' if on else 'aus'}"


@dataclass(frozen=True)
class OverviewEntry:
    chat_id: int
    name: str
    labels: list[str]


def overview(entries: list[OverviewEntry]) -> tuple[str, dict[str, Any] | None]:
    if not entries:
        return NOTHING_ON, None
    shown = entries[:OVERVIEW_MAX]
    lines = [f"• {e.name}: {', '.join(e.labels)}" for e in shown]
    if len(entries) > len(shown):
        lines.append(f"… und {len(entries) - len(shown)} weitere")
    keyboard = {
        "inline_keyboard": [
            [{"text": f"⚙️ {e.name}", "callback_data": open_data(e.chat_id)}]
            for e in shown
        ]
    }
    return "📋 Aktiv:\n\n" + "\n".join(lines), keyboard


# ---------- callback_data ----------
# "t:<chat_id>:<module>.<bucket>" toggles, "o:<chat_id>" opens a menu.
# Worst case ~45 bytes, well under Telegram's 64-byte limit.

def toggle_data(chat_id: int, key: str) -> str:
    return f"t:{chat_id}:{key}"


def open_data(chat_id: int) -> str:
    return f"o:{chat_id}"


@dataclass(frozen=True)
class Callback:
    action: str          # "t" | "o"
    chat_id: int
    key: str | None = None


def parse_callback(data: str) -> Callback | None:
    parts = data.split(":", 2)
    try:
        if parts[0] == "t" and len(parts) == 3 and parts[2]:
            return Callback("t", int(parts[1]), parts[2])
        if parts[0] == "o" and len(parts) == 2:
            return Callback("o", int(parts[1]))
    except ValueError:
        return None
    return None


def display_name(first: str | None, last: str | None, username: str | None) -> str | None:
    name = " ".join(x for x in (first, last) if x)
    return name or (f"@{username}" if username else None)
