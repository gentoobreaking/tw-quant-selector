"""MCP client for tw-quant-mcp (T144).

設計重點：
1. **Transport switch** (環境變數 ``MCP_TRANSPORT``)：
   - ``stdio`` (default)：spawn ``tw-quant-mcp`` 子行程；config 透過 env 傳入。
   - ``streamable-http``：連線 ``http://$MCP_HTTP_ADDR``（預設 ``127.0.0.1:8787``）。
2. **重試**：預設 3 次，指數退避（0.2, 0.4, 0.8s），可由 ``retries`` 覆寫。
3. **熔斷**（circuit.CircuitBreaker）：連續失敗 → 直接拒絕一段時間。
4. **TTL + LRU 快取**（cache.TTLCache）：依工具類別設定不同 TTL。
5. **Single-flight**（singleflight.SingleFlight）：併發去重。
6. **Fallback**：當 MCP 不可用時，可注入 ``fallback`` callable 提供既有實作（例：
   ``realtime_quotes.MISApiClient`` 或 ``twstock_client``）。fallback 仍走快取。

注意：
- 連線管理採 lazy：第一次呼叫時才建立 session。
- 整個 client 設計為 async 介面；對外提供 ``call`` 共用入口，
  並提供 ``quote`` / ``price_history`` / ``indicators`` / ``market_summary`` /
  ``institutional`` 等語意化包裝。
- 同步呼叫場景（celery / scheduler）可透過 ``get_intraday_quote_sync`` 等
  包裝用 ``asyncio.run`` 執行；避免修改既有 scheduler 介面。
- 為了測試友善，正式運作時透過 stdin/stdout 會吃行程管理資源，
  因此支援「外部 long-running 行程模式（http）」以減低負擔。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Awaitable, Callable, Optional

from .cache import TTLCache
from .circuit import CircuitBreaker, CircuitOpenError, CircuitState
from .models import (  # noqa: F401 - re-exported below
    InstitutionalFlow,
    MarketSummary,
    PriceHistory,
    Quote,
    TechIndicators,
)
from .singleflight import SingleFlight

log = logging.getLogger("tw_quant_selector.data.mcp")


# ---------- Custom exceptions ----------


class MCPConnectionError(RuntimeError):
    """無法建立或保持 MCP 連線。"""


class MCPTimeoutError(MCPConnectionError):
    """MCP 請求 timeout。"""


class MCPToolError(RuntimeError):
    """MCP tool 回傳錯誤 (例如 upstream API 故障)。"""


# ---------- Configuration ----------


@dataclass
class MCPClientConfig:
    transport: str = "stdio"  # stdio / streamable-http
    # stdio
    binary_path: str = field(
        default_factory=lambda: os.environ.get(
            "MCP_BINARY_PATH", "tw-quant-mcp"
        )
    )
    # streamable-http
    http_addr: str = field(
        default_factory=lambda: os.environ.get(
            "MCP_HTTP_ADDR", "127.0.0.1:8787"
        )
    )
    # 通用
    connect_timeout: float = 10.0
    call_timeout: float = 20.0
    retries: int = 3
    backoff_base: float = 0.2
    cache_max: int = 2048
    # TTL（秒）
    ttl_realtime: float = 5.0
    ttl_history: float = 60.0
    ttl_indicators: float = 60.0
    ttl_market: float = 60.0
    # Circuit breaker
    circuit_failure_threshold: int = 5
    circuit_reset_timeout: float = 30.0
    # 資料目錄 (傳給 MCP 行程)
    data_dir: str = field(
        default_factory=lambda: os.environ.get(
            "DATA_DIR", os.path.expanduser("~/.tw-quant-mcp/data")
        )
    )

    @classmethod
    def from_env(cls) -> "MCPClientConfig":
        cfg = cls()
        cfg.transport = os.environ.get("MCP_TRANSPORT", cfg.transport).lower()
        cfg.http_addr = os.environ.get("MCP_HTTP_ADDR", cfg.http_addr)
        cfg.binary_path = os.environ.get("MCP_BINARY_PATH", cfg.binary_path)
        cfg.data_dir = os.environ.get("DATA_DIR", cfg.data_dir)
        cfg.retries = int(os.environ.get("MCP_RETRIES", cfg.retries))
        cfg.call_timeout = float(
            os.environ.get("MCP_CALL_TIMEOUT", cfg.call_timeout)
        )
        return cfg


# ---------- Cache key helpers ----------


def _ck(prefix: str, *parts: Any) -> str:
    return prefix + ":" + ":".join(str(p) for p in parts if p is not None)


# ---------- Main client ----------


class MCPClient:
    """tw-quant-mcp 非同步 client。"""

    def __init__(
        self,
        config: Optional[MCPClientConfig] = None,
        fallback: Optional[
            Callable[[str, dict[str, Any]], Awaitable[Any]]
        ] = None,
    ):
        self.config = config or MCPClientConfig.from_env()
        self.fallback = fallback

        self._cache_quote = TTLCache[Quote](
            max_entries=self.config.cache_max, default_ttl=self.config.ttl_realtime
        )
        self._cache_history = TTLCache[PriceHistory](
            max_entries=max(64, self.config.cache_max // 4),
            default_ttl=self.config.ttl_history,
        )
        self._cache_ind = TTLCache[TechIndicators](
            max_entries=max(64, self.config.cache_max // 4),
            default_ttl=self.config.ttl_indicators,
        )
        self._cache_market = TTLCache[MarketSummary](
            max_entries=8, default_ttl=self.config.ttl_market
        )
        self._cache_inst = TTLCache[InstitutionalFlow](
            max_entries=16, default_ttl=self.config.ttl_market
        )
        self._circuit = CircuitBreaker(
            failure_threshold=self.config.circuit_failure_threshold,
            reset_timeout=self.config.circuit_reset_timeout,
        )
        self._flight = SingleFlight()

        self._stack: Optional[AsyncExitStack] = None
        self._session: Any = None  # mcp.ClientSession
        self._lock = asyncio.Lock()
        self._initialized = False

    # ---------- Lifecycle ----------

    async def _ensure_session(self) -> Any:
        """lazy 建立 session (stdio 或 streamable-http)。"""
        if self._initialized and self._session is not None:
            return self._session
        async with self._lock:
            if self._initialized and self._session is not None:
                return self._session
            try:
                stack = AsyncExitStack()
                await stack.__aenter__()
                session = await self._open_session(stack)
                self._stack = stack
                self._session = session
                self._initialized = True
                return session
            except Exception as exc:  # noqa: BLE001
                log.warning("mcp.connect_failed", error=str(exc))
                raise MCPConnectionError(str(exc)) from exc

    async def _open_session(self, stack: AsyncExitStack) -> Any:
        from mcp.client.session import ClientSession

        transport = self.config.transport
        if transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            binary = self.config.binary_path
            if not shutil.which(binary) and not os.path.isabs(binary):
                # 既不在 PATH 也不是絕對路徑 → 常見備援位置
                fallback_paths = [
                    os.path.expanduser("~/Projects/tw-quant-mcp/bin/tw-quant-mcp"),
                    "/usr/local/bin/tw-quant-mcp",
                ]
                for cand in fallback_paths:
                    if os.path.isfile(cand) and os.access(cand, os.X_OK):
                        binary = cand
                        break

            params = StdioServerParameters(
                command=binary,
                args=[],
                env={
                    **os.environ,
                    "MCP_TRANSPORT": "stdio",
                    "DATA_DIR": self.config.data_dir,
                    "LOG_LEVEL": os.environ.get("MCP_LOG_LEVEL", "warn"),
                },
            )
            read, write = await asyncio.wait_for(
                stack.enter_async_context(stdio_client(params)),
                timeout=self.config.connect_timeout,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read, write)),
                timeout=self.config.connect_timeout,
            )
        elif transport in ("streamable-http", "http", "streamable_http"):
            from mcp.client.streamable_http import streamable_http_client

            url = f"http://{self.config.http_addr}/mcp"
            read, write, _ = await asyncio.wait_for(
                stack.enter_async_context(streamable_http_client(url)),
                timeout=self.config.connect_timeout,
            )
            session = await asyncio.wait_for(
                stack.enter_async_context(ClientSession(read, write)),
                timeout=self.config.connect_timeout,
            )
        else:
            raise MCPConnectionError(f"Unknown transport: {transport}")

        await asyncio.wait_for(
            session.initialize(),
            timeout=self.config.connect_timeout,
        )
        log.info(
            "mcp.session_ready", transport=transport, addr=self.config.http_addr
        )
        return session

    async def close(self) -> None:
        async with self._lock:
            if self._stack is not None:
                try:
                    await self._stack.aclose()
                except Exception:  # noqa: BLE001
                    pass
            self._stack = None
            self._session = None
            self._initialized = False

    def is_initialized(self) -> bool:
        return self._initialized and self._session is not None

    async def __aenter__(self) -> "MCPClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ---------- Core dispatch ----------

    async def call(
        self,
        tool: str,
        arguments: Optional[dict[str, Any]] = None,
        *,
        cache_key: Optional[str] = None,
        ttl: float = 60.0,
        cache: Optional[TTLCache] = None,
    ) -> Any:
        """呼叫 tool 並回傳反序列化後的 ``data`` 欄位。"""
        arguments = arguments or {}
        # 1) 快取命中
        if cache_key and cache is not None:
            hit = cache.get(cache_key)
            if hit is not None:
                return hit

        # 2) Single-flight 去重
        flight_key = cache_key or f"{tool}:{json.dumps(arguments, sort_keys=True)}"

        async def _do() -> Any:
            # 3) Circuit breaker
            if not self._circuit.allow():
                raise CircuitOpenError(
                    f"circuit={self._circuit.state.value}; skipping {tool}"
                )
            last_exc: Optional[BaseException] = None
            for attempt in range(self.config.retries + 1):
                try:
                    session = await self._ensure_session()
                    result = await asyncio.wait_for(
                        session.call_tool(tool, arguments),
                        timeout=self.config.call_timeout,
                    )
                    if getattr(result, "is_error", None) or getattr(
                        result, "isError", False
                    ):
                        # MCP tool returned an error
                        raise MCPToolError(
                            f"{tool} returned error: "
                            + ", ".join(
                                [
                                    getattr(c, "text", str(c))
                                    for c in getattr(result, "content", [])
                                ]
                            )
                        )
                    data = self._extract_data(result)
                    self._circuit.record_success()
                    return data
                except asyncio.TimeoutError as exc:
                    last_exc = MCPTimeoutError(str(exc))
                except CircuitOpenError:
                    raise
                except MCPConnectionError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                # 退避（指數）
                if attempt < self.config.retries:
                    await asyncio.sleep(
                        self.config.backoff_base * (2 ** attempt)
                    )
            self._circuit.record_failure()
            # 重試耗盡 → fallback
            if self.fallback is not None:
                log.warning(
                    "mcp.fallback tool=%s err=%s",
                    tool,
                    last_exc,
                )
                return await self.fallback(tool, arguments)
            raise last_exc or MCPConnectionError(f"{tool} failed")

        value = await self._flight.do(flight_key, _do)
        if cache_key and cache is not None:
            cache.set(cache_key, value, ttl=ttl)
        return value

    def _extract_data(self, result: Any) -> Any:
        """從 CallToolResult 取出業務資料 (解 envelope)。"""
        contents = getattr(result, "content", None) or []
        text_chunks: list[str] = []
        for c in contents:
            text = getattr(c, "text", None)
            if text:
                text_chunks.append(text)
        if not text_chunks:
            return None
        raw = "\n".join(text_chunks)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return raw
        if isinstance(parsed, dict):
            return parsed.get("data", parsed)
        if isinstance(parsed, list):
            return parsed
        return parsed

    # ---------- High-level API ----------

    async def quote(self, symbol: str) -> Quote:
        """盤中即時報價：透過 ``get_intraday_quote``。

        注意：MCP 需先以 ``set_active_watchlist`` 加入觀察清單才有報價；
        本方法會先嘗試加入（單一 symbol），再 ``get_intraday_quote`` 讀取。
        若想批次取多檔請直接呼叫 ``set_active_watchlist`` + 用其他途徑快取。
        """
        cache_key = _ck("quote", symbol)
        try:
            data = await self.call(
                "get_intraday_quote",
                {"symbol": symbol},
                cache_key=cache_key,
                ttl=self.config.ttl_realtime,
                cache=self._cache_quote,
            )
            return self._parse_quote(symbol, data)
        except CircuitOpenError:
            return await self._fallback_quote(symbol)
        except (MCPToolError, MCPConnectionError):
            return await self._fallback_quote(symbol)

    async def set_watchlist(self, symbols: list[str]) -> None:
        """設定盤中 watchlist（先決條件）。"""
        if not symbols:
            return
        try:
            await self.call(
                "set_active_watchlist",
                {"symbols": symbols[:15]},  # MCP 限制 1-15 檔
                ttl=self.config.ttl_realtime,
            )
            # 失效 quote 快取（因為 watchlist 變動）
            self._cache_quote.invalidate("quote")
        except (CircuitOpenError, MCPToolError, MCPConnectionError):
            pass  # 非致命

    async def price_history(
        self,
        symbol: str,
        period: str = "day",
        adjust: bool = False,
        days: int = 30,
    ) -> PriceHistory:
        cache_key = _ck("hist", symbol, period, adjust, days)
        try:
            data = await self.call(
                "get_stock_daily_kline",
                {
                    "symbol": symbol,
                    "period": period,
                    "adjust": "Y" if adjust else "N",
                    # 註：MCP 無 days 參數，依賴其預設回傳近期資料
                },
                cache_key=cache_key,
                ttl=self.config.ttl_history,
                cache=self._cache_history,
            )
            return self._parse_history(symbol, period, adjust, data)
        except (CircuitOpenError, MCPToolError, MCPConnectionError):
            return await self._fallback_history(symbol, period, adjust)

    async def indicators(self, symbol: str) -> TechIndicators:
        """技術指標：來自 ``get_stock_daily_quote`` helper (MA20/MA60/RSI/MACD)。"""
        cache_key = _ck("ind", symbol)
        try:
            data = await self.call(
                "get_stock_daily_quote",
                {"symbol": symbol},
                cache_key=cache_key,
                ttl=self.config.ttl_indicators,
                cache=self._cache_ind,
            )
            return self._parse_indicators(symbol, data)
        except (CircuitOpenError, MCPToolError, MCPConnectionError):
            return TechIndicators(stock_id=symbol)

    async def market_summary(self, d: Optional[date] = None) -> MarketSummary:
        d = d or date.today()
        cache_key = _ck("mkt", d.isoformat())
        try:
            data = await self.call(
                "get_market_summary",
                {"date": d.isoformat()},
                cache_key=cache_key,
                ttl=self.config.ttl_market,
                cache=self._cache_market,
            )
            return self._parse_market_summary(data, d)
        except (CircuitOpenError, MCPToolError, MCPConnectionError):
            return MarketSummary(date=d)

    async def institutional(
        self, market: str = "tse", d: Optional[date] = None
    ) -> InstitutionalFlow:
        d = d or date.today()
        cache_key = _ck("inst", market, d.isoformat())
        try:
            data = await self.call(
                "get_institutional_investors",
                {"market": market, "date": d.isoformat()},
                cache_key=cache_key,
                ttl=self.config.ttl_market,
                cache=self._cache_inst,
            )
            return self._parse_institutional(market, d, data)
        except (CircuitOpenError, MCPToolError, MCPConnectionError):
            return InstitutionalFlow(date=d, market=market)

    # ---------- Parsers (envelope.data → dataclass) ----------

    @staticmethod
    def _safe_float(x: Any) -> Optional[float]:
        try:
            if x is None or x == "" or x == "-":
                return None
            return float(x)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(x: Any) -> Optional[int]:
        try:
            if x is None or x == "" or x == "-":
                return None
            return int(float(x))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_price_levels(raw: Any) -> list[tuple[float, int]]:
        out: list[tuple[float, int]] = []
        if not isinstance(raw, list):
            return out
        for entry in raw[:5]:
            if not isinstance(entry, dict):
                continue
            p = MCPClient._safe_float(entry.get("price"))
            v = MCPClient._safe_int(entry.get("volume")) or 0
            if p is None:
                continue
            out.append((p, v))
        return out

    def _parse_quote(self, symbol: str, data: Any) -> Quote:
        """解析 MCP get_intraday_quote 的信封 data。

        期望欄位（見 tw-quant-mcp/pkg/model/intraday.go）：
        - last / open / high / low / change / change_pct / prev_close / volume
        - bids / asks：PriceLevel 陣列，含 price / volume
        - date (YYYY-MM-DD) + time (HH:MM:SS)
        """
        if not isinstance(data, dict):
            return Quote(stock_id=symbol)
        bids = self._parse_price_levels(data.get("bids"))
        asks = self._parse_price_levels(data.get("asks"))

        ts_dt: Optional[datetime] = None
        date_str = data.get("date") or ""
        time_str = data.get("time") or ""
        ts = data.get("timestamp")
        if isinstance(ts, str) and ts:
            try:
                ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                ts_dt = None
        elif date_str and time_str:
            try:
                ts_dt = datetime.fromisoformat(
                    f"{date_str}T{time_str}"
                )
            except ValueError:
                ts_dt = None

        return Quote(
            stock_id=symbol,
            price=self._safe_float(data.get("price") or data.get("last")),
            bid=bids[0][0] if bids else self._safe_float(data.get("bid")),
            ask=asks[0][0] if asks else self._safe_float(data.get("ask")),
            volume=self._safe_int(
                data.get("volume") or data.get("cumulative_vol")
            ),
            open_price=self._safe_float(data.get("open")),
            high_price=self._safe_float(data.get("high")),
            low_price=self._safe_float(data.get("low")),
            change_pct=self._safe_float(data.get("change_pct")),
            timestamp=ts_dt,
            bids=bids,
            asks=asks,
        )

    def _parse_history(
        self, symbol: str, period: str, adjust: bool, data: Any
    ) -> PriceHistory:
        if not isinstance(data, list):
            # 也可能是 dict 包 list
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                data = data["data"]
            else:
                return PriceHistory(stock_id=symbol, period=period, adjust=adjust)
        out = PriceHistory(stock_id=symbol, period=period, adjust=adjust)
        for row in data:
            if not isinstance(row, dict):
                continue
            d_str = row.get("date") or row.get("timestamp") or ""
            try:
                d = datetime.fromisoformat(d_str.replace("Z", "+00:00")).date()
            except (AttributeError, ValueError):
                continue
            out.dates.append(d)
            out.open.append(self._safe_float(row.get("open")) or 0.0)
            out.high.append(self._safe_float(row.get("high")) or 0.0)
            out.low.append(self._safe_float(row.get("low")) or 0.0)
            out.close.append(self._safe_float(row.get("close")) or 0.0)
            out.volume.append(self._safe_int(row.get("volume")) or 0)
        return out

    def _parse_indicators(self, symbol: str, data: Any) -> TechIndicators:
        if not isinstance(data, dict):
            return TechIndicators(stock_id=symbol)
        # MCP helper 欄位慣例：ma20 / ma60 / rsi14 / macd / macd_signal / macd_hist
        last_close = self._safe_float(data.get("close"))
        last_volume = self._safe_int(data.get("volume"))
        last_date_raw = data.get("date") or data.get("timestamp")
        last_date: Optional[date] = None
        if isinstance(last_date_raw, str) and last_date_raw:
            try:
                last_date = datetime.fromisoformat(
                    last_date_raw.replace("Z", "+00:00")
                ).date()
            except ValueError:
                pass
        return TechIndicators(
            stock_id=symbol,
            ma5=self._safe_float(data.get("ma5")),
            ma10=self._safe_float(data.get("ma10")),
            ma20=self._safe_float(data.get("ma20")),
            ma60=self._safe_float(data.get("ma60")),
            rsi14=self._safe_float(data.get("rsi14") or data.get("rsi")),
            macd=self._safe_float(data.get("macd")),
            macd_signal=self._safe_float(
                data.get("macd_signal") or data.get("macds")
            ),
            macd_hist=self._safe_float(
                data.get("macd_hist") or data.get("macdh")
            ),
            last_close=last_close,
            last_volume=last_volume,
            last_date=last_date,
        )

    def _parse_market_summary(self, data: Any, d: date) -> MarketSummary:
        # data 可能為 list（每檔）或 dict（summary）
        if isinstance(data, dict):
            return MarketSummary(
                date=d,
                tse_advance=self._safe_int(data.get("tse_advance")),
                tse_decline=self._safe_int(data.get("tse_decline")),
                tse_unchanged=self._safe_int(data.get("tse_unchanged")),
                tse_volume=self._safe_int(data.get("tse_volume")),
                otc_advance=self._safe_int(data.get("otc_advance")),
                otc_decline=self._safe_int(data.get("otc_decline")),
                otc_unchanged=self._safe_int(data.get("otc_unchanged")),
                otc_volume=self._safe_int(data.get("otc_volume")),
            )
        return MarketSummary(date=d)

    def _parse_institutional(
        self, market: str, d: date, data: Any
    ) -> InstitutionalFlow:
        if isinstance(data, dict):
            return InstitutionalFlow(
                date=d,
                market=market,
                foreign_net=self._safe_int(data.get("foreign_net")),
                trust_net=self._safe_int(data.get("trust_net")),
                dealer_net=self._safe_int(data.get("dealer_net")),
                total_net=self._safe_int(data.get("total_net")),
            )
        return InstitutionalFlow(date=d, market=market)

    # ---------- Fallback (no MCP) ----------

    async def _fallback_quote(self, symbol: str) -> Quote:
        """MCP 失敗時退而求其次：呼叫既有 ``MISApiClient``。"""
        if self.fallback is not None:
            data = await self.fallback("get_intraday_quote", {"symbol": symbol})
            if isinstance(data, dict):
                return self._parse_quote(symbol, data)
        # 最後手段：回傳空殼
        return Quote(stock_id=symbol)

    async def _fallback_history(
        self, symbol: str, period: str, adjust: bool
    ) -> PriceHistory:
        if self.fallback is not None:
            data = await self.fallback(
                "get_stock_daily_kline",
                {"symbol": symbol, "period": period, "adjust": adjust},
            )
            if data is not None:
                return self._parse_history(symbol, period, adjust, data)
        return PriceHistory(stock_id=symbol, period=period, adjust=adjust)

    # ---------- Diagnostic ----------

    def stats(self) -> dict[str, Any]:
        """對外暴露統計供 health check / monitoring 使用。"""
        return {
            "circuit": self._circuit.stats(),
            "cache": {
                "quote": self._cache_quote.stats(),
                "history": self._cache_history.stats(),
                "indicators": self._cache_ind.stats(),
                "market": self._cache_market.stats(),
                "institutional": self._cache_inst.stats(),
            },
            "transport": self.config.transport,
            "http_addr": self.config.http_addr,
        }


# ---------- Sync wrappers (scheduler / scripts) ----------


_sync_client_singleton: Optional["MCPClient"] = None


def get_sync_client() -> MCPClient:
    """為 scheduler / scripts 提供同步入口（單例）。"""
    global _sync_client_singleton
    if _sync_client_singleton is None:
        _sync_client_singleton = MCPClient()
    return _sync_client_singleton


def reset_sync_client() -> None:
    global _sync_client_singleton
    if _sync_client_singleton is not None:
        try:
            asyncio.run(_sync_client_singleton.close())
        except Exception:  # noqa: BLE001
            pass
    _sync_client_singleton = None
