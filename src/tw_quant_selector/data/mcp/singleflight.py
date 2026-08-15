"""Single-flight：防止同一股票並發查詢時對上游造成瀑布效應。

同一 ``key`` 同時有多個 coroutine 呼叫時，只有一個會實際發出請求，
其餘會等待並共用結果。
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


class SingleFlight:
    def __init__(self):
        self._inflight: dict[str, asyncio.Future] = {}

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        existing = self._inflight.get(key)
        if existing is not None and not existing.done():
            return await asyncio.wrap_future(existing)

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._inflight[key] = future
        try:
            result = await fn()
            if not future.done():
                future.set_result(result)
            return result
        except BaseException as exc:  # noqa: BLE001 - propagate to all waiters
            if not future.done():
                future.set_exception(exc)
            raise
        finally:
            # 保留短暫以讓等待者讀到，但下一輪會被覆蓋
            self._inflight.pop(key, None)
