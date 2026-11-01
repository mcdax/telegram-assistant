"""Settings menu module. Per-chat toggles via inline buttons in the agent bot chat.

The owner sends ``/start`` or ``/settings`` to the bot (the same bot the
agent module posts through) and gets a persistent reply keyboard with
Telegram's native chat pickers. Picking a chat opens an inline keyboard
with one ✅/❌ button per toggle exposed by the loaded modules
(``Module.toggles()``); tapping flips the module's state.toml override
(which wins over the config seed lists), then edits the keyboard in
place. This is the only way to switch per-chat features. Callback queries
are first offered to other modules' ``on_bot_callback`` (e.g. meme's preview
buttons) because this is the single process polling the bot token. "📋 Übersicht" lists every chat with at least one feature on.

Transport is Bot API long polling (``getUpdates``) restricted to
``message`` and ``callback_query``; updates from anyone but the owner
are dropped. No token → the module stays inert (logged at init).

Bot token and owner default to ``[modules.agent]``'s ``bot_token_env``
and ``target_chat_id``; both can be overridden in this module's block.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from typing import Any

import aiohttp

from telegram_assistant.markers import Marker
from telegram_assistant.module import BotCallbackHandler, ModuleContext
from telegram_assistant.state import StateWriteError
from telegram_assistant.toggles import Toggle

from . import ui
from .bot_api import BotApi, BotApiError, BotApiLike

ApiFactory = Callable[[aiohttp.ClientSession, str], BotApiLike]

ALLOWED_UPDATES = ["message", "callback_query"]
COMMANDS = [{"command": "settings", "description": "Einstellungen pro Chat"}]


class SettingsMenuModule:
    name = "settings_menu"

    _api_override: ApiFactory | None = None  # test hook

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._api: BotApiLike | None = None
        self._owner_id: int = 0
        self._offset: int = 0
        self._poll_timeout_s: int = 30
        self._retry_initial_s: float = 1.0
        self._retry_max_s: float = 60.0
        self._task: asyncio.Task[None] | None = None
        # Names learned from the chat pickers (users_shared / chat_shared).
        self._names: dict[int, str] = {}

    @property
    def active(self) -> bool:
        return self._api is not None

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        agent_cfg = ctx.services.module_config("agent") if ctx.services else {}

        token_env = cfg.get("bot_token_env") or agent_cfg.get("bot_token_env") or ""
        token = os.environ.get(token_env) if token_env else None
        owner = int(cfg.get("owner_user_id") or agent_cfg.get("target_chat_id") or 0)
        self._poll_timeout_s = int(cfg.get("poll_timeout_s", 30))
        self._retry_initial_s = float(cfg.get("retry_initial_s", 1))
        self._retry_max_s = float(cfg.get("retry_max_s", 60))

        if not token:
            ctx.log.warning(
                "settings_menu: bot_token_env=%r not resolvable — menu disabled",
                token_env,
            )
            return
        if owner <= 0:
            ctx.log.warning(
                "settings_menu: no owner user id (owner_user_id or a positive "
                "[modules.agent].target_chat_id) — menu disabled",
            )
            return
        self._owner_id = owner
        factory = SettingsMenuModule._api_override or BotApi
        self._api = factory(ctx.http, token)
        self._task = asyncio.create_task(self._run(), name="settings_menu.poll")
        ctx.log.info("settings_menu: polling bot updates for owner=%s", owner)

    async def shutdown(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def markers(self) -> list[Marker]:
        return []

    # ---------- polling ----------

    async def _run(self) -> None:
        assert self._ctx is not None and self._api is not None
        try:
            await self._api.call("setMyCommands", commands=COMMANDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._ctx.log.warning("settings_menu: setMyCommands failed: %s", exc)
        await self._poll_forever()

    async def _poll_forever(self) -> None:
        assert self._ctx is not None and self._api is not None
        delay = self._retry_initial_s
        while True:
            try:
                updates = await self._api.call(
                    "getUpdates",
                    timeout_s=self._poll_timeout_s + 15,
                    offset=self._offset,
                    timeout=self._poll_timeout_s,
                    allowed_updates=ALLOWED_UPDATES,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._ctx.log.warning(
                    "settings_menu: getUpdates failed (%s) — retrying in %.0fs",
                    exc, delay,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._retry_max_s)
                continue
            delay = self._retry_initial_s
            for update in updates or []:
                self._offset = max(self._offset, int(update.get("update_id", 0)) + 1)
                try:
                    await self.handle_update(update)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._ctx.log.exception("settings_menu: update handling failed")

    # ---------- update handling ----------

    async def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            await self._on_message(update["message"])
        elif "callback_query" in update:
            await self._on_callback(update["callback_query"])

    def _from_owner(self, obj: dict[str, Any]) -> bool:
        return (obj.get("from") or {}).get("id") == self._owner_id

    async def _on_message(self, m: dict[str, Any]) -> None:
        assert self._ctx is not None
        chat = m.get("chat") or {}
        if not self._from_owner(m) or chat.get("type") != "private":
            self._ctx.log.debug("settings_menu: ignoring message from non-owner")
            return
        to = int(chat["id"])
        text = m.get("text") or ""
        if "users_shared" in m:
            users = m["users_shared"].get("users") or []
            if not users:
                return
            u = users[0]
            chat_id = ui.bot_api_chat_id(u["user_id"])
            name = ui.display_name(u.get("first_name"), u.get("last_name"), u.get("username"))
            if name:
                self._names[chat_id] = name
            await self._send_menu(to, chat_id)
        elif "chat_shared" in m:
            cs = m["chat_shared"]
            chat_id = ui.bot_api_chat_id(cs["chat_id"])
            if cs.get("title"):
                self._names[chat_id] = cs["title"]
            await self._send_menu(to, chat_id)
        elif _is_command(text, "start") or _is_command(text, "settings"):
            await self._call(
                "sendMessage", chat_id=to, text=ui.PICKER_PROMPT,
                reply_markup=ui.picker_keyboard(),
            )
        elif text == ui.BTN_OVERVIEW:
            await self._send_overview(to)

    async def _on_callback(self, q: dict[str, Any]) -> None:
        assert self._ctx is not None
        if not self._from_owner(q):
            self._ctx.log.debug("settings_menu: ignoring callback from non-owner")
            return
        for handler in self._bot_callback_handlers():
            if await handler(q):
                return
        qid = q["id"]
        cb = ui.parse_callback(q.get("data") or "")
        msg = q.get("message") or {}
        if cb is None:
            await self._call("answerCallbackQuery", callback_query_id=qid)
            return
        if cb.action == "o":
            await self._call("answerCallbackQuery", callback_query_id=qid)
            await self._send_menu(int(msg.get("chat", {}).get("id", self._owner_id)), cb.chat_id)
            return

        toggle = next((t for t in self._toggles() if t.key == cb.key), None)
        if toggle is None:
            await self._call(
                "answerCallbackQuery", callback_query_id=qid,
                text="Funktion nicht verfügbar",
            )
            return
        new = not toggle.is_on(cb.chat_id)
        try:
            toggle.set(cb.chat_id, new)
        except StateWriteError:
            await self._call(
                "answerCallbackQuery", callback_query_id=qid,
                text="✗ Speichern fehlgeschlagen", show_alert=True,
            )
            return
        self._ctx.log.info(
            "settings_menu: %s chat=%s → %s", toggle.key, cb.chat_id, "on" if new else "off",
        )
        await self._call(
            "answerCallbackQuery", callback_query_id=qid, text=ui.toast(toggle.label, new),
        )
        if msg.get("message_id") is not None:
            await self._call(
                "editMessageReplyMarkup",
                chat_id=msg["chat"]["id"],
                message_id=msg["message_id"],
                reply_markup=ui.toggle_keyboard(cb.chat_id, self._toggles()),
            )

    # ---------- views ----------

    async def _send_menu(self, to: int, chat_id: int) -> None:
        toggles = self._toggles()
        name = await self._name(chat_id)
        if not toggles:
            await self._call("sendMessage", chat_id=to, text=f"{ui.menu_text(name)}\n\n{ui.NO_FEATURES}")
            return
        await self._call(
            "sendMessage", chat_id=to, text=ui.menu_text(name),
            reply_markup=ui.toggle_keyboard(chat_id, toggles),
        )

    async def _send_overview(self, to: int) -> None:
        toggles = self._toggles()
        chats: set[int] = set()
        for t in toggles:
            chats |= t.chats()
        entries: list[ui.OverviewEntry] = []
        for chat_id in sorted(chats):
            labels = [t.label for t in toggles if t.is_on(chat_id)]
            if labels:
                entries.append(ui.OverviewEntry(chat_id, await self._name(chat_id), labels))
        entries.sort(key=lambda e: e.name.casefold())
        text, keyboard = ui.overview(entries)
        params: dict[str, Any] = {"chat_id": to, "text": text}
        if keyboard is not None:
            params["reply_markup"] = keyboard
        await self._call("sendMessage", **params)

    # ---------- helpers ----------

    def _toggles(self) -> list[Toggle]:
        assert self._ctx is not None
        if self._ctx.services is None:
            return []
        return ui.sort_toggles(self._ctx.services.toggles())

    def _bot_callback_handlers(self) -> list[BotCallbackHandler]:
        """Other modules' inline-button handlers. This module owns the only
        getUpdates loop for the bot token, so it routes their callbacks."""
        assert self._ctx is not None
        services = self._ctx.services
        if services is None or not hasattr(services, "bot_callback_handlers"):
            return []
        return services.bot_callback_handlers()

    async def _name(self, chat_id: int) -> str:
        assert self._ctx is not None
        if chat_id in self._names:
            return self._names[chat_id]
        try:
            name = await self._ctx.tg.get_chat_name(chat_id)
        except Exception as exc:
            self._ctx.log.debug("settings_menu: name lookup chat=%s failed: %s", chat_id, exc)
            name = None
        if name:
            self._names[chat_id] = name
            return name
        return str(chat_id)

    async def _call(self, method: str, **params: Any) -> Any:
        assert self._ctx is not None and self._api is not None
        try:
            return await self._api.call(method, **params)
        except asyncio.CancelledError:
            raise
        except BotApiError as exc:
            if "message is not modified" in exc.description:
                return None
            self._ctx.log.warning("settings_menu: %s", exc)
        except Exception as exc:
            self._ctx.log.warning("settings_menu: %s failed: %s", method, exc)
        return None


def _is_command(text: str, command: str) -> bool:
    head = text.split(maxsplit=1)[0] if text.strip() else ""
    return head.split("@", 1)[0] == f"/{command}"
