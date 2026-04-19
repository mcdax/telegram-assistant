"""Event bus with per-(module, chat_id) task tracking.

dispatch() schedules a handler invocation. If another invocation for the
same (module, chat_id) is still running, it is cancelled first.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

Handler = Callable[[Any], Awaitable[None]]
log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[tuple[str, str], Handler] = {}
        self._tasks: dict[tuple[str, str, int], asyncio.Task] = {}
        self._all_tasks: set[asyncio.Task] = set()

    def subscribe(self, topic: str, module: str, handler: Handler) -> None:
        self._subs[(topic, module)] = handler

    async def dispatch(self, topic: str, module: str, chat_id: int, payload: Any) -> None:
        handler = self._subs.get((topic, module))
        if handler is None:
            return

        key = (topic, module, chat_id)
        prev = self._tasks.get(key)
        if prev is not None and not prev.done():
            prev.cancel()
            try:
                await prev
            except (asyncio.CancelledError, Exception):
                pass

        async def _run() -> None:
            try:
                await handler(payload)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.error("handler for (%s, %s) raised: %s", topic, module, e, exc_info=True)

        task = asyncio.create_task(_run())
        self._tasks[key] = task
        self._all_tasks.add(task)
        task.add_done_callback(self._all_tasks.discard)

    async def drain(self) -> None:
        pending = list(self._all_tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
