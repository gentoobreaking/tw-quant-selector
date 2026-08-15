"""Tests for tw_quant_selector.data.mcp client (T144).

測試策略：
- ``mock Session / call_tool`` 模擬 MCP server 回應
- 驗證：重試、熔斷、快取、Single-flight、fallback、解析器
- 不實際啟動 tw-quant-mcp 行程（CI friendly）
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from tw_quant_selector.data.mcp import (
    MCPClient,
    MCPClientConfig,
    MCPToolError,
    MCPTimeoutError,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    TTLCache,
)
from tw_quant_selector.data.mcp.cache import TTLCache as CacheModule
from tw_quant_selector.data.mcp.circuit import CircuitBreaker as CBModule
from tw_quant_selector.data.mcp.client import MCPClient as ClientModule
from tw_quant_selector.data.mcp.models import (
    InstitutionalFlow,
    MarketSummary,
    PriceHistory,
    Quote,
    TechIndicators,
)


# ---------- Helpers ----------


def _make_call_tool_result(data):
    """模擬 CallToolResult，將 dict/list 序列化成 text。"""
    import json as _json

    from mcp import types

    content = types.TextContent(
        type="text", text=_json.dumps({"data": data, "disclaimer": "僅供研究"})
    )
    return types.CallToolResult(content=[content], isError=False)


def _err_call_tool_result(message: str):
    from mcp import types

    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        is_error=True,
    )


class _FakeSession:
    """取代真實 ClientSession 的輕量假物件。

    ``responder`` 接受 (name, arguments)，回傳 plain dict
    （框架內部會自動以 ``_make_call_tool_result`` 封裝成 CallToolResult）。
    如需直接控制 response 傳入 ``raw_responder``。
    """

    def __init__(self, responder=None, raw_responder=None):
        self.responder = responder or (lambda name, args: {})
        self._raw = raw_responder
        self.call_tool = AsyncMock(side_effect=self._call_tool)
        self.initialize = AsyncMock(return_value={"protocolVersion": "x"})

    async def _call_tool(self, name, arguments=None, **kwargs):
        arguments = arguments or {}
        if self._raw is not None:
            return self._raw(name, arguments)
        return _make_call_tool_result(self.responder(name, arguments))


# ---------- Cache ----------


class TestTTLCache(unittest.TestCase):
    def test_set_get_expire(self):
        c: TTLCache[str] = TTLCache(max_entries=10, default_ttl=0.05)
        c.set("k", "v")
        self.assertEqual(c.get("k"), "v")
        import time as _t

        _t.sleep(0.1)
        self.assertIsNone(c.get("k"))

    def test_lru_eviction(self):
        c: TTLCache[int] = TTLCache(max_entries=3, default_ttl=60)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        c.get("a")  # touch a → b is LRU
        c.set("d", 4)
        self.assertIsNone(c.get("b"))
        self.assertEqual(c.get("a"), 1)

    def test_invalidate_prefix(self):
        c: TTLCache[str] = TTLCache(max_entries=10)
        c.set("foo:1", "a")
        c.set("foo:2", "b")
        c.set("bar:1", "c")
        n = c.invalidate("foo")
        self.assertEqual(n, 2)
        self.assertIsNone(c.get("foo:1"))
        self.assertEqual(c.get("bar:1"), "c")

    def test_stats_hit_rate(self):
        c: TTLCache[str] = TTLCache(max_entries=4, default_ttl=60)
        c.set("k", "v")
        c.get("k")
        c.get("k")
        c.get("missing")
        s = c.stats()
        self.assertEqual(s["hits"], 2)
        self.assertEqual(s["misses"], 1)
        self.assertAlmostEqual(s["hit_rate"], 0.6667, places=3)


# ---------- Circuit Breaker ----------


class TestCircuitBreaker(unittest.TestCase):
    def test_opens_after_threshold(self):
        cb = CircuitBreaker(
            failure_threshold=3, reset_timeout=0.5, success_threshold=1
        )
        self.assertEqual(cb.state, CircuitState.CLOSED)
        for _ in range(3):
            cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertFalse(cb.allow())

    def test_half_open_then_close(self):
        cb = CircuitBreaker(
            failure_threshold=1, reset_timeout=0.1, success_threshold=1
        )
        cb.record_failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        import time as _t

        _t.sleep(0.2)
        self.assertTrue(cb.allow())
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        cb.record_success()
        self.assertEqual(cb.state, CircuitState.CLOSED)


# ---------- Parser tests ----------


class TestParsers(unittest.TestCase):
    def setUp(self):
        self.client = MCPClient(config=MCPClientConfig(retries=0))

    def test_parse_quote_basic(self):
        q = self.client._parse_quote(
            "2330",
            {
                "price": 600.5,
                "open": 595,
                "high": 605,
                "low": 594,
                "volume": 12345,
                "change_pct": 0.83,
                "timestamp": "2026-08-16T09:30:00Z",
            },
        )
        self.assertEqual(q.stock_id, "2330")
        self.assertEqual(q.price, 600.5)
        self.assertEqual(q.open_price, 595.0)
        self.assertEqual(q.volume, 12345)
        self.assertIsInstance(q.timestamp, datetime)

    def test_parse_quote_with_depth(self):
        q = self.client._parse_quote(
            "2330",
            {
                "price": 600,
                "bids": [{"price": 599, "volume": 10, "type": "bid"}],
                "asks": [{"price": 601, "volume": 5, "type": "ask"}],
            },
        )
        self.assertEqual(q.bids, [(599.0, 10)])
        self.assertEqual(q.asks, [(601.0, 5)])

    def test_parse_quote_handles_dash(self):
        q = self.client._parse_quote(
            "2330", {"price": "-", "open": "", "volume": None}
        )
        self.assertIsNone(q.price)
        self.assertIsNone(q.open_price)
        self.assertIsNone(q.volume)

    def test_parse_history_list(self):
        data = [
            {
                "date": "2026-08-15T00:00:00Z",
                "open": 100,
                "high": 110,
                "low": 99,
                "close": 105,
                "volume": 1000,
            },
            {
                "date": "2026-08-16T00:00:00Z",
                "open": 106,
                "high": 112,
                "low": 104,
                "close": 108,
                "volume": 1200,
            },
        ]
        h = self.client._parse_history("2330", "day", False, data)
        self.assertEqual(len(h.dates), 2)
        self.assertEqual(h.close_prices()[0], 105.0)
        self.assertEqual(h.dates[0], date(2026, 8, 15))

    def test_parse_indicators(self):
        ind = self.client._parse_indicators(
            "2330",
            {
                "close": 600,
                "volume": 5000,
                "date": "2026-08-16T00:00:00Z",
                "ma5": 595,
                "ma20": 580,
                "ma60": 555,
                "rsi14": 60.5,
                "macd": 1.2,
            },
        )
        self.assertEqual(ind.last_close, 600.0)
        self.assertEqual(ind.ma20, 580.0)
        self.assertEqual(ind.ma60, 555.0)
        self.assertEqual(ind.rsi14, 60.5)
        self.assertEqual(ind.last_date, date(2026, 8, 16))

    def test_parse_market_summary(self):
        ms = self.client._parse_market_summary(
            {
                "tse_advance": 300,
                "tse_decline": 200,
                "tse_unchanged": 50,
                "otc_advance": 200,
                "otc_decline": 150,
                "otc_unchanged": 30,
            },
            date(2026, 8, 16),
        )
        self.assertEqual(ms.tse_advance, 300)
        self.assertEqual(ms.date, date(2026, 8, 16))


# ---------- Client call dispatch ----------


class _IsolatedClient(MCPClient):
    """跳過 lazy connect，直接注入 fake session 的測試子類別。"""

    def __init__(self, fake_session, **kw):
        cfg = MCPClientConfig(retries=kw.pop("retries", 0))
        super().__init__(config=cfg, **kw)
        self._session = fake_session
        self._initialized = True

    async def _ensure_session(self):
        return self._session


class TestClientCall(unittest.IsolatedAsyncioTestCase):
    async def test_call_basic(self):
        session = _FakeSession(
            responder=lambda n, a: {"hello": "world", **a}
        )
        client = _IsolatedClient(session)
        out = await client.call("test_tool", {"k": "v"})
        self.assertEqual(out["hello"], "world")
        self.assertEqual(out["k"], "v")

    async def test_call_caches(self):
        session = _FakeSession(
            responder=lambda n, a: {"x": 1}
        )
        client = _IsolatedClient(session)
        cache: TTLCache = TTLCache(max_entries=8, default_ttl=60)
        first = await client.call(
            "test", {"a": 1}, cache_key="k", ttl=60, cache=cache
        )
        second = await client.call(
            "test", {"a": 1}, cache_key="k", ttl=60, cache=cache
        )
        self.assertEqual(first, second)
        # Only one upstream call
        self.assertEqual(session.call_tool.await_count, 1)

    async def test_tool_error_raises(self):
        from mcp import types

        session = _FakeSession(
            raw_responder=lambda n, a: types.CallToolResult(
                content=[types.TextContent(type="text", text="boom")],
                is_error=True,
            )
        )
        client = _IsolatedClient(session)
        with self.assertRaises(MCPToolError):
            await client.call("test", {})

    async def test_fallback_on_failure(self):
        session = _FakeSession(responder=lambda n, a: {})
        session.call_tool = AsyncMock(side_effect=RuntimeError("down"))
        called = {}

        async def fb(tool, args):
            called["fb"] = tool
            return {"ok": True}

        client = _IsolatedClient(session, fallback=fb)
        out = await client.call("test", {})
        self.assertEqual(out, {"ok": True})
        self.assertEqual(called["fb"], "test")

    async def test_retry_then_success(self):
        from mcp import types

        attempts = [0]

        async def _call(name, arguments=None, **kwargs):
            attempts[0] += 1
            if attempts[0] < 2:
                raise RuntimeError("transient")
            return types.CallToolResult(
                content=[types.TextContent(type="text", text='{"data": 42}')],
                isError=False,
            )

        session = _FakeSession()
        session.call_tool = AsyncMock(side_effect=_call)
        client = _IsolatedClient(session, retries=2)
        out = await client.call("t", {})
        self.assertEqual(out, 42)
        self.assertEqual(attempts[0], 2)

    async def test_quote_happy_path(self):
        session = _FakeSession(
            responder=lambda n, a: {
                "price": 500.0,
                "open": 495,
                "high": 502,
                "low": 494,
                "volume": 1000,
            }
        )
        client = _IsolatedClient(session)
        q = await client.quote("2330")
        self.assertEqual(q.stock_id, "2330")
        self.assertEqual(q.price, 500.0)

    async def test_quote_fallback_circuit_open(self):
        from mcp import types

        session = _FakeSession()

        async def _err(name, arguments=None, **kwargs):
            raise RuntimeError("down")

        session.call_tool = AsyncMock(side_effect=_err)
        client = _IsolatedClient(
            session,
            fallback=lambda tool, args: asyncio.sleep(0, result={"price": 999}),
        )
        # 直接觸發 quote → fallback
        # 把 fallback 改成 async
        async def _fb(tool, args):
            return {"price": 999}

        client.fallback = _fb
        # 強制 circuit open
        for _ in range(6):
            client._circuit.record_failure()
        q = await client.quote("2330")
        self.assertEqual(q.price, 999)

    async def test_indicators_handles_missing_keys(self):
        session = _FakeSession(responder=lambda n, a: {"ma20": 100})
        client = _IsolatedClient(session)
        ind = await client.indicators("2330")
        self.assertEqual(ind.ma20, 100.0)
        self.assertIsNone(ind.ma60)


# ---------- Single-flight ----------


class TestSingleFlight(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_call_only_one_upstream(self):
        from tw_quant_selector.data.mcp.singleflight import SingleFlight

        sf = SingleFlight()
        calls = [0]

        async def producer():
            calls[0] += 1
            await asyncio.sleep(0.05)
            return 1

        results = await asyncio.gather(
            sf.do("k", producer),
            sf.do("k", producer),
            sf.do("k", producer),
        )
        self.assertEqual(results, [1, 1, 1])
        self.assertEqual(calls[0], 1)


if __name__ == "__main__":
    unittest.main()
