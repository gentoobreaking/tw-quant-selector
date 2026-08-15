"""Fallback adapter: 將既有資料源轉為 MCPClient 接受的 fallback 介面。

呼叫簽名：
    async def fallback(tool: str, arguments: dict) -> Any

支援的 tool 名稱：
- ``get_intraday_quote`` → ``MISApiClient().fetch_all([symbol])`` 的單檔結果
- ``get_stock_daily_kline`` → ``twstock_client.TwStockClient.get_daily(...)``
- ``get_stock_daily_quote`` → ``twstock_client.TwStockClient.get_daily_quote(...)``
- ``get_market_summary`` / ``get_institutional_investors`` → 既有 ``twse_client``

其餘 tool → 回傳 None（呼叫端將以 dataclass 預設值呈現）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from .client import MCPClient


def build_fallback() -> Optional[Callable[[str, dict[str, Any]], Any]]:
    """建立 fallback 回呼。執行緒安全：MIS API 同步呼叫會丟入 thread pool。"""
    try:
        from tw_quant_selector.data.realtime_quotes import (
            MISApiClient,
            _parse_mis_quote,
        )
    except ImportError:  # pragma: no cover
        MISApiClient = None  # type: ignore

    try:
        from tw_quant_selector.data.twstock_client import TwStockClient
    except ImportError:  # pragma: no cover
        TwStockClient = None  # type: ignore

    try:
        from tw_quant_selector.data.twse_client import TwseClient
    except ImportError:  # pragma: no cover
        TwseClient = None  # type: ignore

    async def fallback(tool: str, arguments: dict[str, Any]) -> Any:
        symbol = arguments.get("symbol") or arguments.get("stock_id")

        if tool == "get_intraday_quote" and symbol and MISApiClient is not None:
            client = MISApiClient()
            quotes = await asyncio.to_thread(
                client.fetch_all, [symbol], [symbol]
            )
            if not quotes:
                return None
            q = next((x for x in quotes if x.stock_id == symbol), quotes[0])
            return {
                "price": q.price,
                "open": q.open_price,
                "high": q.high_price,
                "low": q.low_price,
                "volume": q.volume,
                "bid": q.bid,
                "ask": q.ask,
                "change_pct": q.change_pct,
            }

        if (
            tool in ("get_stock_daily_kline", "get_stock_daily_quote")
            and symbol
            and TwStockClient is not None
        ):
            tsc = TwStockClient()
            kline = await asyncio.to_thread(tsc.get_daily, symbol, 60)
            return kline  # 已有對齊 dict list 的格式

        if tool == "get_market_summary" and TwseClient is not None:
            client = TwseClient()
            return await asyncio.to_thread(client.fetch_market_summary)

        if tool == "get_institutional_investors" and TwseClient is not None:
            market = arguments.get("market", "tse")
            client = TwseClient()
            return await asyncio.to_thread(
                client.fetch_institutional, market
            )

        return None

    return fallback
