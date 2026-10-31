"""Tests for the settings menu's pure builders."""
from __future__ import annotations

from telethon.tl.types import PeerChannel, PeerChat, PeerUser
from telethon.utils import get_peer_id

from telegram_assistant.modules.settings_menu import ui
from telegram_assistant.toggles import Toggle


def _toggle(module: str, bucket: str, label: str, on: set[int] | None = None) -> Toggle:
    on = on if on is not None else set()
    return Toggle(
        module=module, bucket=bucket, label=label,
        is_on=lambda c: c in on,
        set=lambda c, v: on.add(c) if v else on.discard(c),
        chats=lambda: set(on),
    )


# ---------- chat id mapping ----------

def test_bot_api_user_id_matches_telethon_chat_id():
    assert ui.bot_api_chat_id(15553658) == get_peer_id(PeerUser(15553658))


def test_bot_api_basic_group_id_matches_telethon_chat_id():
    # Bot API: basic groups are -<chat_id>.
    assert ui.bot_api_chat_id(-4711) == get_peer_id(PeerChat(4711))


def test_bot_api_supergroup_id_matches_telethon_chat_id():
    # Bot API: supergroups/channels are -100<channel_id>.
    assert ui.bot_api_chat_id(-1001234567890) == get_peer_id(PeerChannel(1234567890))


# ---------- keyboards ----------

def test_picker_keyboard_uses_native_pickers():
    kb = ui.picker_keyboard()
    user_btn, group_btn, overview_btn = (row[0] for row in kb["keyboard"])
    assert user_btn["text"] == ui.BTN_PICK_USER
    assert user_btn["request_users"]["request_name"] is True
    assert user_btn["request_users"]["user_is_bot"] is False
    assert group_btn["text"] == ui.BTN_PICK_GROUP
    assert group_btn["request_chat"]["chat_is_channel"] is False
    assert group_btn["request_chat"]["request_title"] is True
    assert overview_btn == {"text": ui.BTN_OVERVIEW}
    assert kb["is_persistent"] is True


def test_toggle_keyboard_reflects_state():
    toggles = [
        _toggle("correcting", "auto_fix", "Korrektur vor dem Senden", on={5}),
        _toggle("drafting", "auto_draft", "Antwortvorschläge"),
    ]
    kb = ui.toggle_keyboard(5, toggles)
    assert kb["inline_keyboard"] == [
        [{"text": "✅ Korrektur vor dem Senden", "callback_data": "t:5:correcting.auto_fix"}],
        [{"text": "❌ Antwortvorschläge", "callback_data": "t:5:drafting.auto_draft"}],
    ]


def test_sort_toggles_uses_menu_order_and_keeps_unknowns_last():
    toggles = [
        _toggle("media_reply", "auto_media", "M"),
        _toggle("other", "x", "X"),
        _toggle("drafting", "auto_draft", "D"),
        _toggle("correcting", "auto_fix_sent", "FS"),
        _toggle("correcting", "auto_fix", "F"),
    ]
    assert [t.label for t in ui.sort_toggles(toggles)] == ["F", "FS", "D", "M", "X"]


def test_toast():
    assert ui.toast("Korrektur vor dem Senden", True) == "Korrektur vor dem Senden: an"
    assert ui.toast("Medien-Antworten", False) == "Medien-Antworten: aus"


# ---------- callback data ----------

def test_callback_roundtrip_supergroup():
    data = ui.toggle_data(-1001234567890, "correcting.auto_fix_sent")
    assert len(data.encode()) <= 64
    assert ui.parse_callback(data) == ui.Callback("t", -1001234567890, "correcting.auto_fix_sent")
    assert ui.parse_callback(ui.open_data(-42)) == ui.Callback("o", -42)


def test_parse_callback_rejects_garbage():
    for bad in ("", "x", "t:abc:k", "t:5", "t:5:", "o:", "o:1:2", "d:5"):
        assert ui.parse_callback(bad) is None, bad


# ---------- overview ----------

def test_overview_empty():
    assert ui.overview([]) == (ui.NOTHING_ON, None)


def test_overview_lists_chats_with_open_buttons():
    text, kb = ui.overview([
        ui.OverviewEntry(5, "Mara", ["Antwortvorschläge"]),
        ui.OverviewEntry(-100777, "Team", ["Korrektur vor dem Senden", "Medien-Antworten"]),
    ])
    assert text == (
        "📋 Aktiv:\n\n"
        "• Mara: Antwortvorschläge\n"
        "• Team: Korrektur vor dem Senden, Medien-Antworten"
    )
    assert kb == {"inline_keyboard": [
        [{"text": "⚙️ Mara", "callback_data": "o:5"}],
        [{"text": "⚙️ Team", "callback_data": "o:-100777"}],
    ]}


def test_overview_truncates():
    entries = [ui.OverviewEntry(i, str(i), ["X"]) for i in range(ui.OVERVIEW_MAX + 3)]
    text, kb = ui.overview(entries)
    assert text.endswith("… und 3 weitere")
    assert kb is not None and len(kb["inline_keyboard"]) == ui.OVERVIEW_MAX


def test_display_name():
    assert ui.display_name("Mara", "M", None) == "Mara M"
    assert ui.display_name(None, None, "mara") == "@mara"
    assert ui.display_name(None, None, None) is None
