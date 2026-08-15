"""MCP-aware realtime quote adapter。

将 MCP 的即時資料接口封裝成既有 ``RealtimeQuote`` 形状，
供 ``realtime_quotes.poll_realtime`` / ``MISApiClient.fetch_all``
在不破坏既有 DB/呼叫者契约的前提下，透過 MCP 取得資料。

设计重点：
- 保持現有 ``RealtimeQuote`` dataclass 形状 100% 向下相容
- 提供 async 批量取得方法，因為 MCP client 是 async
- 失敗時直接傳回 partial results（None/None 价者），由呼叫端
  fallback 到既有 MIS 逻辑
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from .client import MCPClient, MCPClientConfig

if TYPE_CHECKING:
    from tw_quant_selector.data.realtime_quotes import RealtimeQuote


_MCP_CLIENT: Optional[MCPClient] = None
_INIT_LOCK = asyncio.Lock()


def _client_factory() -> MCPClient:
    """依環境變數組裝 MCPClient，並注入 ``MISApiClient`` 作為 fallback。"""
    from .fallback import wrap_mis_api_client

    cfg = MCPClientConfig.from_env()
    fallback = wrap_mis_api_client()
    return MCPClient(config=cfg, fallback=fallback)


async def get_client() -> MCPClient:
    """Lazy singleton，避免重複初始化 stdio 子行程。"""
    global _MCP_CLIENT
    if _MCP_CLIENT is not None and _MCP_CLIENT.is_initialized():
        return _MCP_CLIENT
    async with _INIT_LOCK:
        if _MCP_CLIENT is None or not _MCP_CLIENT.is_initialized():
            client = await asyncio.to_thread(_client_factory)
            # 在 to_thread 內建立，避免連接被 event loop 卡住
            _MCP_CLIENT = client
        return _MCP_CLIENT


def is_mcp_enabled() -> bool:
    """讀取 ``TW_USE_MCP`` / ``USE_MCP_QUOTES`` 控制是否走 MCP。"""
    return (
        os.environ.get("TW_USE_MCP", "").lower() in ("1", "true", "yes")
        or os.environ.get("USE_MCP_QUOTES", "").lower() in ("1", "true", "yes")
    )


def _quote_to_realtime_quote(q):
    """將 MCP ``Quote`` 轉成既有 ``RealtimeQuote``。

    使用 ``__import__`` 動態導入，避免 ``mcp`` 套件內部任何時候
    都必須有 ``structlog`` / ``httpx`` / ``duckdb`` 等重型依賴。
    """
    realtime_quotes_module = __import__(
        "tw_quant_selector.data.realtime_quotes", fromlist=["RealtimeQuote"]
    )
    RealtimeQuote = realtime_quotes_module.RealtimeQuote

    change_amt = None
    return RealtimeQuote(
        stock_id=q.stock_id,
        price=q.price,
        volume=q.volume,
        bid=q.bid,
        ask=q.ask,
        change_amt=change_amt,
        change_pct=q.change_pct,
        trade_volume=None,
        timestamp=q.timestamp,
        open_price=q.open_price,
        high_price=q.high_price,
        low_price=q.low_price,
    )


async def fetch_quotes_async(stock_ids: list[str]) -> list["RealtimeQuote"]:
    """透過 MCP 取得一批即時報價。

    任一檔失敗仍回傳成功的部分，失敗者以 ``price=None`` 的
    ``RealtimeQuote`` 表示；呼叫端可以與原 MIS 結果對齊。
    """
    if not stock_ids:
        return []
    client = await get_client()
    sem = asyncio.Semaphore(8)

    async def _one(sid: str) -> RealtimeQuote:
        async with sem:
            try:
                q = await client.quote(sid)
                if q.price is None:
                    return RealtimeQuote(stock_id=sid)
                return _quote_to_realtime_quote(q)
            except Exception:
                return RealtimeQuote(stock_id=sid)

    results = await asyncio.gather(*(_one(s) for s in stock_ids))
    return list(results)


async def fetch_quotes_with_fallback(
    stock_ids: list[str],
    *,
    fallback_fn,
    key_stock_ids: Optional[list[str]] = None,
) -> list[RealtimeQuote]:
    """從 MCP 取資料；當 MCP 完全失敗時呼叫 ``fallback_fn``（即 MISApiClient.fetch_all）。

    ``fallback_fn(stock_ids, key_stock_ids)`` 必須回傳 list[RealtimeQuote]。
    """
    if not stock_ids:
        return []
    try:
        quotes = await fetch_quotes_async(stock_ids)
        if any(q.price is not None for q in quotes):
            return quotes
    except Exception:
        pass
    return fallback_fn(stock_ids, key_stock_ids or [])