"""Domain models exposed by MCPClient.

設計原則：
- 與 tw-quant-selector 既有 ``RealtimeQuote`` / ``daily_prices`` 結構相容
- 欄位命名採 snake_case，與既有的 Python 模組風格一致
- 所有欄位皆 Optional，避免上游 schema 變動時拋例外
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional


@dataclass
class Quote:
    """盤中即時報價（對應 MCP ``get_intraday_quote``）。"""

    stock_id: str
    price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: Optional[int] = None
    open_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    change_pct: Optional[float] = None
    timestamp: Optional[datetime] = None
    # 盤中五檔（簡化版：只存前五檔 best bid/ask 價量）
    bids: list[tuple[float, int]] = field(default_factory=list)
    asks: list[tuple[float, int]] = field(default_factory=list)


@dataclass
class PriceHistory:
    """歷史 K 線（對應 MCP ``get_stock_daily_kline``）。"""

    stock_id: str
    period: str = "day"  # day / week / month
    dates: list[date] = field(default_factory=list)
    open: list[float] = field(default_factory=list)
    high: list[float] = field(default_factory=list)
    low: list[float] = field(default_factory=list)
    close: list[float] = field(default_factory=list)
    volume: list[int] = field(default_factory=list)
    adjust: bool = False

    def close_prices(self) -> list[float]:
        return list(self.close)


@dataclass
class TechIndicators:
    """技術指標（對應 MCP ``get_stock_daily_quote`` 內含 helper）。

    註：MCP 沒有獨立的 ``best_four_points`` 工具，但 ``get_stock_daily_quote``
    會回傳 MA20 / MA60 / RSI14 / MACD 等 helper 指標。本資料結構沿用既有的
    ``BestFourPoints`` 命名以避免呼叫端大規模重構（欄位全為 Optional）。
    """

    stock_id: str
    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    rsi14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    last_close: Optional[float] = None
    last_volume: Optional[int] = None
    last_date: Optional[date] = None


@dataclass
class MarketSummary:
    """全市場盤後摘要（對應 MCP ``get_market_summary``）。"""

    date: Optional[date] = None
    tse_advance: Optional[int] = None
    tse_decline: Optional[int] = None
    tse_unchanged: Optional[int] = None
    tse_volume: Optional[int] = None
    otc_advance: Optional[int] = None
    otc_decline: Optional[int] = None
    otc_unchanged: Optional[int] = None
    otc_volume: Optional[int] = None


@dataclass
class InstitutionalFlow:
    """三大法人買賣超彙總（對應 MCP ``get_institutional_investors``）。"""

    date: Optional[date] = None
    market: str = "tse"  # tse / otc
    foreign_net: Optional[int] = None
    trust_net: Optional[int] = None
    dealer_net: Optional[int] = None
    total_net: Optional[int] = None
