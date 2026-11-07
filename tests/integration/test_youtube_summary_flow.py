"""Full draft → loader → native API → private preview → owner button routing."""
import asyncio
import json

import aiohttp
import pytest
from aioresponses import aioresponses

from telegram_assistant.app import App
from telegram_assistant.events import DraftUpdate
from telegram_assistant.modules.settings_menu.module import SettingsMenuModule
from telegram_assistant.modules.youtube_summary import module as youtube
from telegram_assistant.modules.youtube_summary.gemini import API_BASE
from tests.fakes.bot_api import OWNER, FakeBotApi, callback
from tests.fakes.llm import fake_llm
from tests.fakes.telegram import FakeTelegramClient, make_message


@pytest.fixture
async def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('BOT_TOKEN', 'test-bot')
    monkeypatch.setenv('GEMINI_API_KEY', 'test-gemini')
    api = FakeBotApi()
    monkeypatch.setattr(SettingsMenuModule, '_api_override', lambda *_: api)
    monkeypatch.setattr(youtube, 'BotApi', lambda *_: api)
    tg = FakeTelegramClient()
    async with aiohttp.ClientSession() as http:
        app = App(tg=tg, llm=fake_llm('FIXED'), http=http, state_path=tmp_path / 'state.toml')
        await app.start({
            'agent': {'enabled': False, 'bot_token_env': 'BOT_TOKEN', 'target_chat_id': OWNER},
            'settings_menu': {'enabled': True},
            'correcting': {'enabled': True, 'system_prompt': 'fix', 'context_last_n': 0},
            'youtube_summary': {'enabled': True, 'debounce_s': 0.03, 'cooldown_s': 0},
        })
        yield app, tg, api
        await app.stop()


async def test_draft_preview_and_owner_send_via_existing_poller(setup):
    app, tg, api = setup
    tg.seed_history(42, [make_message(42, 'alice', 'https://youtu.be/iw9SZgyahqg')])
    with aioresponses() as mock:
        mock.post(API_BASE + '/gemini-3.8-flash:generateContent', payload={
            'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps({
                'available': True, 'summary': 'Laut Video: theoretischer Fortschritt, keine praktische Beschleunigung.',
            })}]}}],
        })
        await app.inject_draft_update(DraftUpdate(42, '/youtube'))
        await app.drain()
        sends = await api.wait_for('sendMessage', 2)
    preview = sends[-1]
    assert tg.drafts[42] == '' and not tg.sent
    module = next(m for m in app._modules if m.name == 'youtube_summary')
    pending = next(iter(module._pending.values()))
    data = preview['reply_markup']['inline_keyboard'][0][0]['callback_data']
    # Foreign user must be rejected by the actual poller, not just the module.
    api.push(callback(data, from_id=99, message_id=pending.preview_message_id))
    api.push(callback(data, message_id=pending.preview_message_id))
    await api.wait_for('editMessageReplyMarkup')
    assert len(tg.sent) == 1 and tg.sent[0].chat_id == 42
    assert 'Laut Video' in tg.sent[0].text


async def test_switching_to_other_marker_cancels_youtube_timer(setup):
    app, tg, api = setup
    await app.inject_draft_update(DraftUpdate(42, '/youtube'))
    await app.drain()
    await app.inject_draft_update(DraftUpdate(42, '/fix helo'))
    await app.drain()
    await asyncio.sleep(0.06)
    assert not api.of('sendMessage')
    assert tg.drafts[42] == 'FIXED'
