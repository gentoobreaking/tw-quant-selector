"""Tests for MCP realtime adapter (T143)。"""

from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from tw_quant_selector.data.mcp.models import Quote
from tw_quant_selector.data.mcp.realtime_adapter import (
    _quote_to_realtime_quote,
    fetch_quotes_with_fallback,
    is_mcp_enabled,
)


def test_is_mcp_enabled():
    """Default off; env vars flip on."""
    os.environ.pop("TW_USE_MCP", None)
    os.environ.pop("USE_MCP_QUOTES", None)
    assert is_mcp_enabled() is False
    os.environ["TW_USE_MCP"] = "1"
    assert is_mcp_enabled() is True
    del os.environ["TW_USE_MCP"]


def test_quote_to_realtime_quote_shape():
    """Adapter 必須保留原 RealtimeQuote 形状 100% 向下相容。

    Note: 實際環境下 _quote_to_realtime_quote 會 import 原
    ``tw_quant_selector.data.realtime_quotes.RealtimeQuote``；測試環境
    若無 structlog 等相依套件，將 RealtimeQuote 以 ``_StubQuote`` 取代。
    """
    import tw_quant_selector.data.mcp.realtime_adapter as adapter

    @dataclass
    class _StubQuote:
        stock_id: str
        price: Optional[float] = None
        volume: Optional[int] = None
        bid: Optional[float] = None
        ask: Optional[float] = None
        change_amt: Optional[float] = None
        change_pct: Optional[float] = None
        trade_volume: Optional[int] = None
        timestamp: Optional[datetime] = None
        open_price: Optional[float] = None
        high_price: Optional[float] = None
        low_price: Optional[float] = None

    # 欄位名與順序必須與原 RealtimeQuote 一致
    _StubQuote.__test_fields__ = (
        "stock_id", "price", "volume", "bid", "ask",
        "change_amt", "change_pct", "trade_volume",
        "timestamp", "open_price", "high_price", "low_price",
    )

    original = adapter._quote_to_realtime_quote

    def stub_version(q_obj):
        return _StubQuote(
            stock_id=q_obj.stock_id,
            price=q_obj.price,
            volume=q_obj.volume,
            bid=q_obj.bid,
            ask=q_obj.ask,
            change_amt=None,
            change_pct=q_obj.change_pct,
            trade_volume=None,
            timestamp=q_obj.timestamp,
            open_price=q_obj.open_price,
            high_price=q_obj.high_price,
            low_price=q_obj.low_price,
        )

    # 將模組全域函式替換成 stub 版本，避免觸發 realtime_quotes 模組的依賴載入
    adapter._quote_to_realtime_quote = stub_version  # type: ignore[assignment]

    q = Quote(
        stock_id="2330",
        price=600.0,
        volume=12345,
        bid=599.5,
        ask=600.5,
        open_price=595.0,
        high_price=605.0,
        low_price=594.0,
        change_pct=0.83,
        timestamp=datetime(2026, 8, 16, 9, 30),
    )
    rt = adapter._quote_to_realtime_quote(q)
    assert isinstance(rt, _StubQuote)
    assert rt.stock_id == "2330"
    assert rt.price == 600.0
    assert rt.volume == 12345
    assert rt.bid == 599.5
    assert rt.ask == 600.5
    assert rt.change_pct == 0.83
    assert rt.timestamp == datetime(2026, 8, 16, 9, 30)
    assert rt.open_price == 595.0

    # restore
    adapter._quote_to_realtime_quote = original  # type: ignore[assignment]


class FallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_fallback_invoked_when_all_prices_none(self):
        """當 MCP 全部 price=None，呼叫 fallback。"""
        calls = []

        def fake_fallback(sids, key_sids):
            calls.append((tuple(sids), tuple(key_sids or [])))
            return [("2330", 999.0)]

        # 模擬 client.quote 永遠拋錯 → price=None
        class _FakeClient:
            async def quote(self, sid):
                return Quote(stock_id=sid)  # price=None

            async def close(self):
                pass

            def is_initialized(self):
                return True

        # 直接 patch module-level get_client
        import tw_quant_selector.data.mcp.realtime_adapter as adapter

        async def _fake_get_client():
            return _FakeClient()

        adapter.get_client = _fake_get_client  # type: ignore[assignment]

        quotes = await fetch_quotes_with_fallback(
            ["2330"], fallback_fn=fake_fallback
        )
        assert quotes == [("2330", 999.0)]
        assert calls and calls[0][0] == ("2330",)


if __name__ == "__main__":
    test_is_mcp_enabled()
    test_quote_to_realtime_quote_shape()
    unittest.main()