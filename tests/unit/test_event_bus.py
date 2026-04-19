from __future__ import annotations

import asyncio
import pytest

from telegram_assistant.event_bus import EventBus


async def test_subscribe_and_dispatch():
    bus = EventBus()
    calls = []

    async def handler(event: str) -> None:
        calls.append(event)

    bus.subscribe("topic", "modA", handler)
    await bus.dispatch("topic", "modA", chat_id=1, payload="hello")
    await bus.drain()
    assert calls == ["hello"]


async def test_handler_exception_isolated_and_logged(caplog):
    bus = EventBus()
    ok_calls = []

    async def bad(event):
        raise RuntimeError("boom")

    async def ok(event):
        ok_calls.append(event)

    bus.subscribe("t", "bad", bad)
    bus.subscribe("t", "ok", ok)
    await bus.dispatch("t", "bad", chat_id=1, payload="x")
    await bus.dispatch("t", "ok", chat_id=1, payload="x")
    await bus.drain()
    assert ok_calls == ["x"]
    assert any("boom" in rec.message for rec in caplog.records)


async def test_inflight_cancelled_for_same_pair():
    bus = EventBus()
    first_started = asyncio.Event()
    cancelled = False

    async def slow(event):
        nonlocal cancelled
        first_started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled = True
            raise

    bus.subscribe("t", "m", slow)
    await bus.dispatch("t", "m", chat_id=1, payload="first")
    await first_started.wait()
    await bus.dispatch("t", "m", chat_id=1, payload="second")
    await bus.drain()
    assert cancelled is True


async def test_different_pairs_run_concurrently():
    bus = EventBus()
    running = 0
    peak = 0

    async def handler(event):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1

    bus.subscribe("t", "m", handler)
    await bus.dispatch("t", "m", chat_id=1, payload="a")
    await bus.dispatch("t", "m", chat_id=2, payload="b")
    await bus.drain()
    assert peak == 2
