"""Sender names must survive both history and live-event conversion."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.tl import types

from telegram_assistant.telethon_client import TelethonTelegramClient, _sender_identity


def source(sender, *, sender_id=42, resolved=None, **kwargs):
    return SimpleNamespace(sender=sender, sender_id=sender_id,
                           get_sender=AsyncMock(return_value=resolved), **kwargs)


@pytest.mark.parametrize(('entity', 'expected'), [
    (types.User(id=42, first_name='Max', last_name='Mustermann', username='max123'), 'Max Mustermann'),
    (types.User(id=42, first_name='Max'), 'Max'),
    (types.User(id=42, last_name='Mustermann'), 'Mustermann'),
    (types.User(id=42, username='max123'), 'max123'),
    (types.User(id=42), '42'),
    (types.ChannelForbidden(id=42, access_hash=1, title='Kanalname'), 'Kanalname'),
])
async def test_cached_entity_label(entity, expected):
    item = source(entity)
    assert (await _sender_identity(item))[0] == expected
    item.get_sender.assert_not_awaited()


async def test_missing_entity_resolved_on_demand():
    item = source(None, resolved=types.User(id=42, first_name='Max'))
    assert await _sender_identity(item) == ('Max', None)
    item.get_sender.assert_awaited_once()


async def test_failed_resolution_falls_back_without_losing_message():
    item = source(None)
    item.get_sender.side_effect = ValueError('unavailable')
    assert await _sender_identity(item) == ('42', None)
    assert await _sender_identity(source(None, sender_id=None)) == ('unknown', None)


def message(**kwargs):
    return source(None, id=7, date=datetime(2026, 11, 1, tzinfo=timezone.utc),
                  message='Hallo', out=False, reply_to=None, **kwargs)


async def test_history_resolves_once_per_sender_and_keeps_chronology():
    client = object.__new__(TelethonTelegramClient)
    newest = message(resolved=types.User(id=42, first_name='Max', last_name='Mustermann', username='max123'))
    older = message()
    older.id = 6

    async def iter_messages(chat_id, limit):
        assert chat_id == 99 and limit == 20
        yield newest
        yield older

    client._client = SimpleNamespace(iter_messages=iter_messages)
    history = await client.fetch_history(99, 20)
    assert [m.message_id for m in history] == [6, 7]
    assert [m.sender for m in history] == ['Max Mustermann', 'Max Mustermann']
    assert all(m.sender_id == 42 and m.sender_username == 'max123' for m in history)
    newest.get_sender.assert_awaited_once()
    older.get_sender.assert_not_awaited()


async def test_live_event_uses_same_display_name():
    client = object.__new__(TelethonTelegramClient)
    event = source(None, resolved=types.User(id=42, first_name='Max', username='max123'),
                   chat_id=99, message=message())
    converted = await client._to_message(event)
    assert converted.sender == 'Max' and converted.sender_id == 42
    assert converted.sender_username == 'max123'


async def test_identity_preserves_both_display_name_and_username():
    item = source(types.User(id=42, first_name='Max', last_name='Mustermann', username='max123'))
    assert await _sender_identity(item) == ('Max Mustermann', 'max123')
