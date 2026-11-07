"""/youtube [URL] [question] → private preview → explicit owner-approved send.

Uses settings_menu's existing Bot API poller. No second getUpdates consumer.
Pending summaries are memory-only and expire on TTL, reload or restart.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from telegram_assistant.events import DraftUpdate
from telegram_assistant.markers import Marker, MarkerMatch, MatchKind
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.settings_menu.bot_api import BotApi

from .gemini import SummaryError, URL_RE, extract_url, summarize

PREFIX = 'youtube:'
NO_BUTTONS = {'inline_keyboard': []}


def clip(text: str, units: int) -> str:
    """Telegram counts UTF-16 units (astral characters use two)."""
    raw = text.encode('utf-16-le')
    if len(raw) <= units * 2:
        return text
    return raw[:(units - 30) * 2].decode('utf-16-le', errors='ignore') + '\n… (gekürzt)'


@dataclass
class PendingSummary:
    id: str
    source_chat_id: int
    text: str
    created: float
    preview_message_id: int = 0


class YoutubeSummaryModule:
    name = 'youtube_summary'

    def __init__(self) -> None:
        self._ctx: ModuleContext | None = None
        self._api: Any = None
        self._timers: dict[int, asyncio.Task] = {}
        self._running: dict[int, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._pending: dict[str, PendingSummary] = {}
        self._last_start: dict[int, float] = {}

    async def init(self, ctx: ModuleContext) -> None:
        self._ctx = ctx
        cfg = ctx.config
        agent = ctx.services.module_config('agent') if ctx.services else {}
        settings = ctx.services.module_config('settings_menu') if ctx.services else {}
        self._owner = int(cfg.get('target_chat_id') or agent.get('target_chat_id') or 0)
        token_env = cfg.get('bot_token_env') or agent.get('bot_token_env') or 'AGENT_BOT_TOKEN'
        token = os.environ.get(token_env, '')
        self._key_env = str(cfg.get('api_key_env') or 'GEMINI_API_KEY')
        self._key = os.environ.get(self._key_env, '')
        if self._owner <= 0 or not token:
            raise ValueError('youtube_summary requires a private target_chat_id and bot token')
        # The existing poller must own this exact bot and owner for the send button.
        poll_token_env = settings.get('bot_token_env') or agent.get('bot_token_env') or ''
        poll_owner = int(settings.get('owner_user_id') or agent.get('target_chat_id') or 0)
        if (not settings.get('enabled') or poll_owner != self._owner
                or os.environ.get(poll_token_env, '') != token):
            raise ValueError('youtube_summary requires settings_menu with the same bot token and owner')
        self._api = BotApi(ctx.http, token)
        self._model = str(cfg.get('model') or 'gemini-3.8-flash')
        self._instruction = str(cfg.get('instruction') or '')
        self._debounce_s = float(cfg.get('debounce_s', 3))
        self._cooldown_s = float(cfg.get('cooldown_s', 30))
        self._timeout_s = float(cfg.get('timeout_s', 180))
        self._ttl_s = float(cfg.get('pending_ttl_s', 3600))
        self._last_n = int(cfg.get('last_n', 30))
        if min(self._debounce_s, self._cooldown_s) < 0 or min(self._timeout_s, self._ttl_s, self._last_n) <= 0:
            raise ValueError('youtube_summary: invalid timing/history settings')
        self._marker = Marker('youtube', cfg.get('markers', {}).get('youtube', '/youtube'), MatchKind.CONTAINS, 55)

    def markers(self) -> list[Marker]:
        return [self._marker]

    async def shutdown(self) -> None:
        tasks = {*self._timers.values(), *self._running.values(), *self._tasks}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._timers.clear()
        self._running.clear()
        self._tasks.clear()
        self._pending.clear()

    async def on_draft_update(self, event: DraftUpdate, match: MarkerMatch) -> None:
        self._cancel_timer(event.chat_id)
        task = asyncio.create_task(self._debounced(event.chat_id, match.remainder))
        self._timers[event.chat_id] = task

    async def on_plain_draft_update(self, event: DraftUpdate) -> None:
        self._cancel_timer(event.chat_id)

    async def on_marker_lost(self, event: DraftUpdate) -> None:
        self._cancel_timer(event.chat_id)

    def _cancel_timer(self, chat_id: int) -> None:
        task = self._timers.pop(chat_id, None)
        if task:
            task.cancel()

    async def _debounced(self, chat_id: int, request: str) -> None:
        current = asyncio.current_task()
        try:
            await asyncio.sleep(self._debounce_s)
            assert self._ctx is not None
            if self._running.get(chat_id) and not self._running[chat_id].done():
                await self._post('⏳ Für diesen Chat läuft bereits eine Video-Zusammenfassung.')
                return
            last = self._last_start.get(chat_id)
            if last is not None and time.monotonic() - last < self._cooldown_s:
                await self._post('⏳ YouTube: Bitte kurz warten (Cooldown).')
                return
            await self._ctx.tg.write_draft(chat_id, '')
            self._last_start[chat_id] = time.monotonic()
            task = self._spawn(self._generate(chat_id, request))
            self._running[chat_id] = task
            task.add_done_callback(lambda t: self._running.pop(chat_id, None)
                                   if self._running.get(chat_id) is t else None)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._ctx.log.warning('youtube_summary: trigger failed', exc_info=False)
            await self._post('❌ YouTube: Anfrage konnte nicht gestartet werden.')
        finally:
            if self._timers.get(chat_id) is current:
                self._timers.pop(chat_id, None)

    async def _resolve(self, chat_id: int, request: str) -> tuple[str, str]:
        assert self._ctx is not None
        selected = extract_url(request)
        if selected:
            return selected
        if URL_RE.search(request):
            raise SummaryError('Kein gültiger YouTube-Videolink in deiner Anfrage.')
        history = await self._ctx.tg.fetch_history(chat_id, self._last_n)
        # Do not assume backend ordering. Message IDs are ordered within each chat.
        for message in sorted(history, key=lambda m: m.message_id, reverse=True):
            selected = extract_url(message.text)
            if not selected and message.attachment and message.attachment.url:
                selected = extract_url(message.attachment.url)
            if selected:
                return selected[0], request.strip()
        raise SummaryError('Kein YouTube-Link im aktuellen Chat gefunden. Nutze /youtube <Link>.')

    async def _generate(self, chat_id: int, request: str) -> None:
        assert self._ctx is not None
        try:
            if not self._key:
                raise SummaryError(f'API-Key fehlt: Umgebungsvariable {self._key_env}.')
            url, question = await self._resolve(chat_id, request)
            await self._post('⏳ Analysiere das YouTube-Video mit Gemini…')
            summary = await summarize(
                self._ctx.http, api_key=self._key, model=self._model, url=url,
                question=question, instruction=self._instruction, timeout_s=self._timeout_s,
            )
            name = await self._ctx.tg.get_chat_name(chat_id) or str(chat_id)
            text = f'YouTube-Zusammenfassung\n{url}\n\n{clip(summary, 3000)}\n\nQuelle: Videoaussagen, kein Faktencheck.'
            self._prune()
            pending = PendingSummary(secrets.token_hex(8), chat_id, text, time.monotonic())
            result = await self._api.call(
                'sendMessage', chat_id=self._owner,
                text=f'Vorschau für: {clip(name, 160)}\n\n{text}',
                reply_markup={'inline_keyboard': [[{
                    'text': '📤 In Chat senden', 'callback_data': f'{PREFIX}{pending.id}:send',
                }]]},
                link_preview_options={'is_disabled': True},
            )
            pending.preview_message_id = int(result['message_id'])
            self._pending[pending.id] = pending
        except asyncio.CancelledError:
            raise
        except SummaryError as exc:
            await self._post(f'❌ YouTube: {exc}')
        except Exception:
            # Do not echo HTTP exception URLs, tokens, or private content.
            self._ctx.log.warning('youtube_summary: generation/preview failed', exc_info=False)
            await self._post('❌ YouTube: Verarbeitung oder Vorschau fehlgeschlagen. Bitte erneut versuchen.')

    async def _post(self, text: str) -> None:
        try:
            await self._api.call('sendMessage', chat_id=self._owner, text=text)
        except Exception:
            if self._ctx:
                self._ctx.log.warning('youtube_summary: private notification failed', exc_info=False)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _prune(self) -> None:
        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if now - p.created < self._ttl_s}

    async def on_bot_callback(self, query: dict[str, Any]) -> bool:
        data = query.get('data') or ''
        if not data.startswith(PREFIX):
            return False
        message = query.get('message') or {}
        # Defense in depth, even though settings_menu already checks the owner.
        if (query.get('from') or {}).get('id') != self._owner or (message.get('chat') or {}).get('id') != self._owner:
            return True
        self._prune()
        parts = data.split(':')
        pending = self._pending.get(parts[1]) if len(parts) == 3 and parts[2] == 'send' else None
        if pending is None or message.get('message_id') != pending.preview_message_id:
            await self._ack(query, '⌛ Vorschau abgelaufen. Bitte /youtube neu starten.')
            return True
        # Consume synchronously before the first await: double-clicks cannot send twice.
        self._pending.pop(pending.id)
        self._spawn(self._send(pending, message))
        await self._ack(query, '📤 Wird gesendet…')
        return True

    async def _ack(self, query: dict[str, Any], text: str) -> None:
        with contextlib.suppress(Exception):
            await self._api.call('answerCallbackQuery', callback_query_id=query.get('id'), text=text)

    async def _send(self, pending: PendingSummary, message: dict[str, Any]) -> None:
        assert self._ctx is not None
        try:
            await self._ctx.tg.send_message(pending.source_chat_id, text=pending.text)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._pending[pending.id] = pending
            await self._post('❌ YouTube: Versand nicht bestätigt. Bitte Zielchat prüfen, bevor du erneut sendest.')
            return
        # Editing the preview can fail after delivery. Never re-enable sending then.
        with contextlib.suppress(Exception):
            await self._api.call('editMessageReplyMarkup', chat_id=self._owner,
                                 message_id=message['message_id'], reply_markup=NO_BUTTONS)
        await self._post('✅ YouTube-Zusammenfassung in den ursprünglichen Chat gesendet.')
