"""Correcting module.

Owns:
  /fix                 — one-shot rewrite. If seen in a draft: overwrite the
                         draft with the corrected remainder. If seen in an
                         already-sent message: edit the sent message in place
                         with the corrected remainder.
  /auto_fix on|off     — toggle per-chat pre-send autofix (rewrites every draft)
  /auto_fix_sent on|off — toggle per-chat post-send autofix (edits every sent message)
"""
from __future__ import annotations

from telegram_assistant.events import DraftUpdate, OutgoingMessage
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.state import StateWriteError


DEFAULT_MARKERS = {
    "fix": "/fix",
    "auto_fix_on": "/auto_fix on",
    "auto_fix_off": "/auto_fix off",
    "auto_fix_sent_on": "/auto_fix_sent on",
    "auto_fix_sent_off": "/auto_fix_sent off",
}

_AUTO_FIX_BUCKET = "auto_fix"
_AUTO_FIX_SENT_BUCKET = "auto_fix_sent"


class CorrectingModule:
    name = "correcting"

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._markers: list[Marker] = []

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        user_markers = ctx.config.get("markers", {})

        def trigger(key: str) -> str:
            return user_markers.get(key, DEFAULT_MARKERS[key])

        self._markers = [
            Marker(name="fix", trigger=trigger("fix"), kind=MatchKind.CONTAINS, priority=70),
            Marker(
                name="auto_fix_on", trigger=trigger("auto_fix_on"),
                kind=MatchKind.EXACT, priority=100,
            ),
            Marker(
                name="auto_fix_off", trigger=trigger("auto_fix_off"),
                kind=MatchKind.EXACT, priority=100,
            ),
            Marker(
                name="auto_fix_sent_on", trigger=trigger("auto_fix_sent_on"),
                kind=MatchKind.EXACT, priority=100,
            ),
            Marker(
                name="auto_fix_sent_off", trigger=trigger("auto_fix_sent_off"),
                kind=MatchKind.EXACT, priority=100,
            ),
        ]

    async def shutdown(self) -> None:
        return

    def markers(self) -> list[Marker]:
        return list(self._markers)

    async def on_draft_update(self, event: DraftUpdate, match: MarkerMatch) -> None:
        assert self._ctx is not None
        name = match.marker.name
        self._ctx.log.debug(
            "on_draft_update chat=%s marker=%s", event.chat_id, name,
        )
        if name == "fix":
            await self._fix_remainder(event.chat_id, match.remainder)
        elif name == "auto_fix_on":
            await self._set_toggle(event.chat_id, _AUTO_FIX_BUCKET, True, "Auto-fix")
        elif name == "auto_fix_off":
            await self._set_toggle(event.chat_id, _AUTO_FIX_BUCKET, False, "Auto-fix")
        elif name == "auto_fix_sent_on":
            await self._set_toggle(event.chat_id, _AUTO_FIX_SENT_BUCKET, True, "Auto-fix-sent")
        elif name == "auto_fix_sent_off":
            await self._set_toggle(event.chat_id, _AUTO_FIX_SENT_BUCKET, False, "Auto-fix-sent")

    async def on_plain_draft_update(self, event: DraftUpdate) -> None:
        """Pre-send autofix: rewrite every draft in chats where auto_fix is on."""
        assert self._ctx is not None
        if not self._is_on(event.chat_id, _AUTO_FIX_BUCKET):
            return
        text = event.text.strip()
        if not text:
            return
        self._ctx.log.debug("auto_fix rewriting draft chat=%s input_len=%d", event.chat_id, len(text))
        corrected = await self._correct(text)
        if corrected is None:
            return
        if corrected.strip() == text:
            self._ctx.log.debug("auto_fix: llm returned same text chat=%s — skipping", event.chat_id)
            return
        await self._ctx.tg.write_draft(event.chat_id, corrected)

    async def on_outgoing_message(self, event: OutgoingMessage) -> None:
        """Post-send corrections:
           1. If the sent text contains the /fix marker → strip the marker,
              correct the remainder, edit the sent message in place.
           2. Otherwise, if auto_fix_sent is on for this chat → correct the
              full text and edit the sent message.
        The /fix path takes precedence so explicit invocations win over the
        per-chat blanket toggle.
        """
        assert self._ctx is not None
        msg = event.message
        fix_marker = self._fix_marker()
        matched, remainder = fix_marker.match(msg.text) if fix_marker else (False, None)
        if matched:
            await self._fix_sent_via_marker(msg.chat_id, msg.message_id, remainder or "")
            return
        if not self._is_on(msg.chat_id, _AUTO_FIX_SENT_BUCKET):
            return
        text = msg.text.strip()
        if not text:
            return
        self._ctx.log.debug(
            "auto_fix_sent rewriting chat=%s id=%s input_len=%d",
            msg.chat_id, msg.message_id, len(text),
        )
        corrected = await self._correct(text)
        if corrected is None:
            return
        if corrected.strip() == text:
            self._ctx.log.debug(
                "auto_fix_sent: llm returned same text chat=%s id=%s — skipping",
                msg.chat_id, msg.message_id,
            )
            return
        await self._ctx.tg.edit_message(msg.chat_id, msg.message_id, corrected)

    async def _fix_sent_via_marker(
        self, chat_id: int, message_id: int, remainder: str
    ) -> None:
        assert self._ctx is not None
        text = remainder.strip()
        if not text:
            self._ctx.log.info(
                "/fix in sent message chat=%s id=%s: empty remainder — ignored",
                chat_id, message_id,
            )
            return
        self._ctx.log.debug(
            "/fix in sent message chat=%s id=%s remainder_len=%d",
            chat_id, message_id, len(text),
        )
        corrected = await self._correct(text)
        if corrected is None:
            return
        # Even if the LLM returned identical text, we still edit so the
        # /fix marker is removed from the sent message.
        await self._ctx.tg.edit_message(chat_id, message_id, corrected)

    def _fix_marker(self) -> Marker | None:
        for m in self._markers:
            if m.name == "fix":
                return m
        return None

    async def _fix_remainder(self, chat_id: int, remainder: str) -> None:
        assert self._ctx is not None
        text = remainder.strip()
        if not text:
            self._ctx.log.info("/fix with empty remainder — ignored")
            return
        corrected = await self._correct(text)
        if corrected is None:
            return
        # Even if the LLM returned identical text, we still write so the
        # /fix marker is removed from the draft.
        await self._ctx.tg.write_draft(chat_id, corrected)

    async def _correct(self, text: str) -> str | None:
        assert self._ctx is not None
        agent = self._ctx.llm.agent(self._ctx.config["system_prompt"])
        try:
            return await self._ctx.llm.run(agent, text)
        except Exception as e:
            self._ctx.log.warning("correcting failed: %s", e)
            return None

    async def _set_toggle(
        self, chat_id: int, bucket: str, on: bool, label: str
    ) -> None:
        assert self._ctx is not None
        try:
            self._ctx.state.set(bucket, str(chat_id), on)
            word = "enabled" if on else "disabled"
            text = f"✓ {label} {word} for this chat"
        except StateWriteError:
            text = "✗ state write failed"
        await self._ctx.tg.write_draft(chat_id, text)

    def _is_on(self, chat_id: int, bucket: str) -> bool:
        assert self._ctx is not None
        return bool(self._ctx.state.get(bucket, str(chat_id), default=False))
