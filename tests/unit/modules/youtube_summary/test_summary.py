from __future__ import annotations

import asyncio
import json
import logging

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.events import Attachment, DraftUpdate
from telegram_assistant.markers import MarkerMatch
from telegram_assistant.module import ModuleContext
from telegram_assistant.modules.youtube_summary.gemini import (
    API_BASE, SummaryError, canonical_url, summarize,
)
from telegram_assistant.modules.youtube_summary.module import YoutubeSummaryModule, clip
from telegram_assistant.state import RuntimeState
from tests.fakes.bot_api import OWNER, FakeBotApi, callback
from tests.fakes.telegram import FakeTelegramClient, make_message

URL = 'https://www.youtube.com/watch?v=iw9SZgyahqg'
URL2 = 'https://www.youtube.com/watch?v=abcdefghijk'
ENDPOINT = API_BASE + '/gemini-3.8-flash:generateContent'


@pytest.mark.parametrize('url', [
    'https://youtu.be/iw9SZgyahqg?is=tracking', URL,
    'https://m.youtube.com/watch?v=iw9SZgyahqg&t=20',
    'https://youtube.com/shorts/iw9SZgyahqg',
    'https://www.youtube.com/live/iw9SZgyahqg',
    'https://www.youtube.com/embed/iw9SZgyahqg',
])
def test_canonical_video_urls(url):
    assert canonical_url(url) == URL


@pytest.mark.parametrize('url', [
    'https://youtube.com.evil.org/watch?v=iw9SZgyahqg',
    'https://youtube.com@evil.org/watch?v=iw9SZgyahqg',
    'https://youtube.com/playlist?list=abc', 'file:///tmp/test',
    'https://youtu.be/short', 'https://youtube.com:8443/watch?v=iw9SZgyahqg',
    'https://youtu.be/iw9SZgyahqg/other',
])
def test_reject_non_video_urls(url):
    assert canonical_url(url) is None


def answer(text='Das Video behauptet einen theoretischen Durchbruch.', **kw):
    return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
        {'text': json.dumps({'available': True, 'summary': text, **kw})},
    ]}}]}


@pytest.fixture
async def http():
    async with aiohttp.ClientSession() as session:
        yield session


async def test_native_video_request(http):
    with aioresponses() as mock:
        mock.post(ENDPOINT, payload=answer())
        text = await summarize(http, api_key='secret', model='gemini-3.8-flash', url=URL, question='Kosten?')
        request = next(iter(mock.requests.values()))[0].kwargs
        assert request['headers']['x-goog-api-key'] == 'secret'
        assert request['json']['contents'][0]['parts'] == [
            {'file_data': {'file_uri': URL}}, {'text': 'Kosten?'},
        ]
        assert 'theoretischen' in text


@pytest.mark.parametrize('status', [400, 403, 404, 429, 500])
async def test_api_errors_hide_response_secrets(http, status):
    with aioresponses() as mock:
        mock.post(ENDPOINT, status=status, payload={'error': {'message': 'secret-token'}})
        with pytest.raises(SummaryError) as exc:
            await summarize(http, api_key='secret-token', model='gemini-3.8-flash', url=URL)
        assert str(status) in str(exc.value)
        assert 'secret-token' not in str(exc.value)


@pytest.mark.parametrize('payload', [
    {}, {'candidates': []}, answer(available=False), answer(''),
    {'candidates': [{'finishReason': 'MAX_TOKENS'}]},
    {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': 'bad JSON'}]}}]},
])
async def test_unusable_responses_fail_closed(http, payload):
    with aioresponses() as mock:
        mock.post(ENDPOINT, payload=payload)
        with pytest.raises(SummaryError):
            await summarize(http, api_key='secret', model='gemini-3.8-flash', url=URL)


class Services:
    def module_config(self, name):
        return {'agent': {'target_chat_id': OWNER, 'bot_token_env': 'BOT_TOKEN'},
                'settings_menu': {'enabled': True}}.get(name, {})


@pytest.fixture
async def setup_module(tmp_path, http, monkeypatch):
    monkeypatch.setenv('BOT_TOKEN', 'bot-secret')
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-secret')
    tg = FakeTelegramClient()
    tg.names[42] = 'Source chat'
    ctx = ModuleContext(tg=tg, llm=None, http=http, config={
        'debounce_s': 0.01, 'cooldown_s': 0,
    }, state=RuntimeState(tmp_path / 'state.toml').for_module('youtube_summary'),
       log=logging.getLogger('youtube'), services=Services())
    mod = YoutubeSummaryModule()
    await mod.init(ctx)
    api = FakeBotApi()
    mod._api = api
    yield mod, tg, api
    await mod.shutdown()


async def generate(mod, request=''):
    with aioresponses() as mock:
        mock.post(ENDPOINT, payload=answer())
        await mod._generate(42, request)


async def test_private_preview_last_link_and_send_once(setup_module):
    mod, tg, api = setup_module
    # Deliberately newest-first, to test order independence.
    tg.seed_history(42, [make_message(42, 'alice', URL, message_id=20),
                         make_message(42, 'bob', URL2, message_id=10)])
    await generate(mod, 'Worum geht es?')
    assert not tg.sent
    preview = api.of('sendMessage')[-1]
    assert URL in preview['text'] and URL2 not in preview['text']
    data = preview['reply_markup']['inline_keyboard'][0][0]['callback_data']
    pending = next(iter(mod._pending.values()))
    query = callback(data, message_id=pending.preview_message_id)['callback_query']
    await mod.on_bot_callback(query)
    await mod.on_bot_callback(query)
    await asyncio.gather(*mod._tasks)
    assert len(tg.sent) == 1
    assert tg.sent[0].chat_id == 42 and tg.sent[0].text == pending.text


async def test_explicit_url_skips_history_and_discards_tracking(setup_module):
    mod, tg, api = setup_module
    await generate(mod, 'https://youtu.be/iw9SZgyahqg?is=tracking Kosten?')
    assert URL in api.of('sendMessage')[-1]['text']
    assert 'tracking' not in api.of('sendMessage')[-1]['text']


async def test_history_embedded_link(setup_module):
    mod, tg, _ = setup_module
    tg.seed_history(42, [make_message(42, 'alice', 'Video', attachment=Attachment('weblink', 'link', URL))])
    assert await mod._resolve(42, 'Kosten?') == (URL, 'Kosten?')


async def test_invalid_explicit_url_does_not_pick_old_video(setup_module):
    mod, tg, _ = setup_module
    tg.seed_history(42, [make_message(42, 'alice', URL)])
    with pytest.raises(SummaryError):
        await mod._resolve(42, 'https://example.org/not-youtube')


async def test_no_link_or_key_reports_error_without_posting_to_source(setup_module):
    mod, tg, api = setup_module
    await mod._generate(42, '')
    assert 'Kein YouTube-Link' in api.of('sendMessage')[-1]['text']
    mod._key = ''
    await mod._generate(42, URL)
    assert 'API-Key fehlt' in api.of('sendMessage')[-1]['text']
    assert not tg.sent and not mod._pending


async def test_foreign_owner_wrong_message_and_expired_button_cannot_send(setup_module):
    mod, tg, api = setup_module
    await generate(mod, URL)
    pending = next(iter(mod._pending.values()))
    data = f'youtube:{pending.id}:send'
    await mod.on_bot_callback(callback(data, from_id=99, message_id=pending.preview_message_id)['callback_query'])
    await mod.on_bot_callback(callback(data, message_id=0)['callback_query'])
    assert len(mod._pending) == 1
    pending.created -= 4000
    await mod.on_bot_callback(callback(data, message_id=pending.preview_message_id)['callback_query'])
    assert not mod._pending and not tg.sent


async def test_send_failure_keeps_preview_for_explicit_retry(setup_module):
    mod, tg, _ = setup_module
    await generate(mod, URL)
    pending = next(iter(mod._pending.values()))
    async def fail(*a, **kw):
        raise ConnectionError('token must not leak')
    tg.send_message = fail
    await mod.on_bot_callback(callback(f'youtube:{pending.id}:send', message_id=pending.preview_message_id)['callback_query'])
    await asyncio.gather(*mod._tasks)
    assert pending.id in mod._pending


async def test_removing_marker_cancels_debounce(setup_module):
    mod, tg, api = setup_module
    marker = mod.markers()[0]
    await mod.on_draft_update(DraftUpdate(42, '/youtube'), MarkerMatch(mod.name, marker, ''))
    await mod.on_plain_draft_update(DraftUpdate(42, 'ordinary text'))
    await asyncio.sleep(0.03)
    assert not api.calls and not tg.drafts


async def test_full_debounce_and_busy_guard(setup_module):
    mod, tg, api = setup_module
    marker = mod.markers()[0]
    with aioresponses() as mock:
        mock.post(ENDPOINT, payload=answer())
        await mod.on_draft_update(DraftUpdate(42, '/youtube ' + URL), MarkerMatch(mod.name, marker, URL))
        await api.wait_for('sendMessage', 2)
    assert tg.drafts[42] == '' and len(mod._pending) == 1
    task = asyncio.create_task(asyncio.Event().wait())
    mod._running[42] = task
    await mod._debounced(42, URL)
    assert 'bereits' in api.of('sendMessage')[-1]['text']


def test_emoji_output_within_telegram_utf16_limit():
    text = clip('😀' * 5000, 3000)
    assert len(text.encode('utf-16-le')) // 2 <= 3000
    assert text.endswith('(gekürzt)')
