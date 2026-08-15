from __future__ import annotations

import collections.abc
import concurrent.futures
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dtime
from typing import Any, Optional
import httpx
import structlog

log = structlog.get_logger()

MIS_BASE = "https://mis.twse.com.tw/stock/api"

# Special stock_id mapping for non-standard symbols in MIS API
# Internal stock_id → MIS symbol suffix (without tse_ prefix)
SPECIAL_MIS_IDS: dict[str, str] = {
    "^TWII": "t00",
}
_REVERSE_MIS: dict[str, str] = {v: k for k, v in SPECIAL_MIS_IDS.items()}
BATCH_SIZE = 50
INTERVAL_SEC = 1.0
MARKET_OPEN = dtime(9, 0)
MARKET_CLOSE = dtime(13, 30)

# Proxy support: set HTTPS_PROXY env var to route MIS API through a proxy
_MIS_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("MIS_PROXY")

# Global health tracker for MIS API
_MIS_HEALTHY = True
_MIS_HEALTH_LAST_CHECK = 0.0
_MIS_HEALTH_COOLDOWN = 300.0  # 5 min cooldown after failure


@dataclass
class RealtimeQuote:
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


def is_market_open(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    t = now.time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


def is_trading_day(check_date: Optional[date] = None) -> bool:
    check_date = check_date or date.today()
    return check_date.weekday() < 5


def _parse_mis_quote(raw: dict[str, str]) -> RealtimeQuote:
    stock_id = raw.get("c", "")
    if stock_id.startswith("tse_"):
        stock_id = stock_id[4:]
    elif stock_id.startswith("otc_"):
        stock_id = stock_id[4:]
    stock_id = stock_id.replace(".tw", "")
    # Reverse-map special MIS symbols to internal stock IDs
    stock_id = _REVERSE_MIS.get(stock_id, stock_id)

    z = raw.get("z")
    price = float(z) if z and z != "-" else None

    v = raw.get("v")
    volume = int(v) if v and v != "-" else None

    tv = raw.get("tv")
    trade_volume = int(tv) if tv and tv != "-" else None

    b = raw.get("b", "")
    bid_parts = b.split("_") if "_" in b else [b]
    bid = float(bid_parts[0]) if bid_parts and bid_parts[0] and bid_parts[0] != "-" else None

    a = raw.get("a", "")
    ask_parts = a.split("_") if "_" in a else [a]
    ask = float(ask_parts[0]) if ask_parts and ask_parts[0] and ask_parts[0] != "-" else None

    change_amt = None
    change_pct = None
    if price is not None:
        y = raw.get("y")
        y_price = float(y) if y and y != "-" else None
        if y_price and y_price != 0:
            change_amt = round(price - y_price, 2)
            change_pct = round((change_amt / y_price) * 100, 2)

    ts_str = raw.get("tlong")
    timestamp: Optional[datetime] = None
    if ts_str and ts_str != "-":
        try:
            timestamp = datetime.fromtimestamp(int(ts_str) / 1000)
        except (ValueError, OSError):
            pass

    o = raw.get("o")
    open_price = float(o) if o and o != "-" else None

    h = raw.get("h")
    high_price = float(h) if h and h != "-" else None

    l = raw.get("l")
    low_price = float(l) if l and l != "-" else None

    return RealtimeQuote(
        stock_id=stock_id,
        price=price,
        volume=volume,
        bid=bid,
        ask=ask,
        change_amt=change_amt,
        change_pct=change_pct,
        trade_volume=trade_volume,
        timestamp=timestamp,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
    )


_MIS_PREFIX_CACHE: dict[str, str] = {}

def _get_mis_prefix(stock_id: str) -> str:
    """Cached lookup: TSE → 'tse', OTC → 'otc'."""
    if stock_id in _MIS_PREFIX_CACHE:
        return _MIS_PREFIX_CACHE[stock_id]
    from tw_quant_selector.data.database import get_session
    session = get_session()
    try:
        row = session.execute(
            "SELECT market FROM stocks WHERE stock_id = ?", [stock_id]
        ).fetchone()
        prefix = "otc" if (row and row[0] == "OTC") else "tse"
    finally:
        session.close()
    _MIS_PREFIX_CACHE[stock_id] = prefix
    return prefix


def _mis_client(timeout: float = 15) -> httpx.Client:
    """Create httpx client with optional proxy."""
    kwargs: dict[str, Any] = {"timeout": timeout}
    if _MIS_PROXY:
        kwargs["proxy"] = _MIS_PROXY
    return httpx.Client(**kwargs)


def _fetch_batch(stock_ids: list[str]) -> list[RealtimeQuote]:
    """Fetch multiple stocks in a single batch request.

    NOTE: The batch endpoint returns reference data (volume, open, high, low)
    for ALL stocks, but z (current price) is always '-'.
    Use _fetch_z for individual price queries.
    """
    if not stock_ids:
        return []
    mis_ids = [SPECIAL_MIS_IDS.get(sid, sid) for sid in stock_ids]
    ex_ch = "|".join(f"tse_{mid}.tw" for mid in mis_ids)
    for attempt in range(3):
        try:
            with _mis_client(timeout=15) as client:
                resp = client.get(
                    f"{MIS_BASE}/getStockInfo.jsp",
                    params={"ex_ch": ex_ch, "json": "1"},
                )
                resp.raise_for_status()
                raw_list = resp.json().get("msgArray", [])
                return [_parse_mis_quote(r) for r in raw_list]
        except Exception:
            if attempt == 2:
                return []
            time.sleep(1)
    return []


def _fetch_z(stock_id: str) -> RealtimeQuote | None:
    """Fetch a single stock to get the real z (current price).

    Individual queries return the actual z value; batch queries don't.
    Returns None on any failure (avoids rate-limit escalation).
    """
    mis_id = SPECIAL_MIS_IDS.get(stock_id, stock_id)
    prefix = _get_mis_prefix(stock_id)
    ex_ch = f"{prefix}_{mis_id}.tw"
    try:
        with _mis_client(timeout=5) as client:
            resp = client.get(
                f"{MIS_BASE}/getStockInfo.jsp",
                params={"ex_ch": ex_ch, "json": "1"},
            )
            resp.raise_for_status()
            raw_list = resp.json().get("msgArray", [])
            if raw_list:
                return _parse_mis_quote(raw_list[0])
    except Exception:
        pass
    return None


def _is_mis_healthy() -> bool:
    """Check if MIS API was recently healthy; reset on cooldown expiry."""
    global _MIS_HEALTHY, _MIS_HEALTH_LAST_CHECK
    if not _MIS_HEALTHY and time.time() - _MIS_HEALTH_LAST_CHECK < _MIS_HEALTH_COOLDOWN:
        return False
    _MIS_HEALTHY = True  # allow a probe
    return True


def _mark_mis_unhealthy():
    global _MIS_HEALTHY, _MIS_HEALTH_LAST_CHECK
    _MIS_HEALTHY = False
    _MIS_HEALTH_LAST_CHECK = time.time()


class MISApiClient:
    """Two-phase fetcher:

    1. Batch request for ALL stocks → gets volume, open, high, low (but z='-').
    2. Individual requests for KEY stocks only → gets real z (current price).

    T143: 可選走 MCP 路徑。設定 ``TW_USE_MCP=1`` 時優先以 tw-quant-mcp
    取得即時報價；若 MCP 無回應/連不上，自動降級至 MIS API。
    """

    def __init__(self, batch_size: int = BATCH_SIZE):
        self.batch_size = batch_size

    def _batch_all(self, stock_ids: list[str]) -> dict[str, RealtimeQuote]:
        """Fetch ALL stocks via batch requests (no real z)."""
        if not _is_mis_healthy():
            log.warning("mis.skipped_batch_health")
            return {}
        result: dict[str, RealtimeQuote] = {}
        for i in range(0, len(stock_ids), self.batch_size):
            batch = stock_ids[i : i + self.batch_size]
            quotes = _fetch_batch(batch)
            if not quotes:
                _mark_mis_unhealthy()
                return result  # return partial results if any
            for q in quotes:
                result[q.stock_id] = q
            if i + self.batch_size < len(stock_ids):
                time.sleep(0.3)
        return result

    def _fetch_key_z(
        self, key_stock_ids: list[str], quota: int = 5
    ) -> dict[str, RealtimeQuote]:
        """Fetch real z prices for key stocks (serial, rate-limited).
        
        ``key_stock_ids`` should contain portfolio holdings + benchmarks.
        """
        if not _is_mis_healthy():
            return {}
        key = list(dict.fromkeys(key_stock_ids))[:quota]
        result: dict[str, RealtimeQuote] = {}
        for sid in key:
            q = _fetch_z(sid)
            if q:
                result[sid] = q
            else:
                _mark_mis_unhealthy()
                break
            time.sleep(0.6)  # be gentle to the API
        return result

    def _fetch_via_mcp(
        self, stock_ids: list[str], key_stock_ids: list[str] | None = None
    ) -> list[RealtimeQuote]:
        """T143: 改由 tw-quant-mcp 取得報價。失敗一律 raise 交給 caller fallback。"""
        from tw_quant_selector.data.mcp.realtime_adapter import (
            fetch_quotes_async,
            _quote_to_realtime_quote,
        )
        sids = list(dict.fromkeys(list(stock_ids) + list(key_stock_ids or [])))
        sids = [s for s in sids if not s.startswith("^")]
        quotes = fetch_quotes_async(sids)
        return [_quote_to_realtime_quote(q) for q in quotes if q.price is not None]

    def fetch_all(self, stock_ids: list[str], key_stock_ids: list[str] | None = None) -> list[RealtimeQuote]:
        # T143 MCP-first path
        if os.environ.get("TW_USE_MCP", "").lower() in ("1", "true", "yes"):
            try:
                mcp_quotes = self._fetch_via_mcp(stock_ids, key_stock_ids)
                if mcp_quotes:
                    return mcp_quotes
                log.warning("mis.mcp_empty_fallback_mis")
            except Exception as exc:  # noqa: BLE001 - fallback is best-effort
                log.warning("mis.mcp_failed_fallback_mis", error=str(exc))

        base = self._batch_all(stock_ids)
        z_map = self._fetch_key_z(key_stock_ids or stock_ids[:5], quota=5)
        # Merge z into base results
        for sid, q in z_map.items():
            if q.price is not None and sid in base:
                base[sid].price = q.price
                if q.change_amt is not None:
                    base[sid].change_amt = q.change_amt
                    base[sid].change_pct = q.change_pct
        return list(base.values())


def get_mcp_status() -> dict[str, Any]:
    """T143: 回傳 MCP 連線健康狀態，供前端狀態面板使用。

    不會主動連線，僅讀取 adapter 內部狀態。
    """
    try:
        from tw_quant_selector.data.mcp.realtime_adapter import is_mcp_enabled
        enabled = is_mcp_enabled()
        return {
            "mcp_enabled": enabled,
            "healthy": None,  # requires live call; reserved for future ping
        }
    except Exception as exc:  # noqa: BLE001
        return {"mcp_enabled": False, "healthy": False, "error": str(exc)}


def poll_realtime(
    db,
    stock_ids: Optional[list[str]] = None,
    picks: Optional[list[str]] = None,
    on_quotes: collections.abc.Callable[[list[dict[str, Any]]], None] | None = None,
    key_stock_ids: Optional[list[str]] = None,
) -> dict[str, Any]:
    if not is_market_open():
        return {"status": "skipped", "reason": "market closed"}

    from tw_quant_selector.data.database import get_session
    from tw_quant_selector.data.realtime_valuation import compute_realtime_valuation

    client = MISApiClient()
    all_quotes = client.fetch_all(stock_ids or [], key_stock_ids=key_stock_ids)
    if not all_quotes:
        return {"status": "empty", "count": 0}

    now = datetime.now()
    session = get_session()
    try:
        count = 0
        quote_data: list[dict[str, Any]] = []
        for q in all_quotes:
            # Skip special indices not in stocks table (e.g. ^TWII)
            if q.stock_id in SPECIAL_MIS_IDS or q.stock_id.startswith("^"):
                continue
            if q.price is not None:
                val = compute_realtime_valuation(
                    db, q.stock_id, q.price, as_of_datetime=now,
                )
                pe = float(val.pe_rt) if val.pe_rt is not None else None
                pb = float(val.pb_rt) if val.pb_rt is not None else None
                dy = float(val.yield_rt) if val.yield_rt is not None else None
            else:
                pe = pb = dy = None
            session.execute(
                """INSERT INTO realtime_quotes
                   (stock_id, quote_time, price, volume, bid, ask, change_amt, change_pct, is_close, pe_realtime, pb_realtime, yield_realtime, open_price, high_price, low_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [q.stock_id, now, q.price, q.volume, q.bid, q.ask, q.change_amt, q.change_pct, False, pe, pb, dy, q.open_price, q.high_price, q.low_price],
            )
            count += 1
            quote_data.append({
                "stock_id": q.stock_id,
                "price": q.price,
                "change_pct": q.change_pct,
                "pe_realtime": pe,
                "pb_realtime": pb,
                "volume": q.volume,
            })
        session.commit()
        with_price = sum(1 for q in all_quotes if q.price is not None)
        log.info("mis.poll_complete", total=len(all_quotes), with_price=with_price, count=count)
        if on_quotes and quote_data:
            on_quotes(quote_data)
        return {"status": "ok", "count": count}
    except Exception as exc:
        session.rollback()
        log.error("mis.save_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        session.close()


def save_intraday_snapshot(db, stock_ids: Optional[list[str]] = None) -> dict[str, Any]:
    if not is_market_open():
        return {"status": "skipped", "reason": "market closed"}

    from tw_quant_selector.data.database import get_session

    now = datetime.now()
    cutoff = now - timedelta(seconds=90)
    session = get_session()
    try:
        rows = session.execute(
            """SELECT DISTINCT ON (stock_id) stock_id, price, volume, bid, ask, change_amt, change_pct
               FROM realtime_quotes
               WHERE quote_time >= ?
               ORDER BY stock_id, quote_time DESC""",
            [cutoff],
        ).fetchall()

        snapshot_count = 0
        for r in rows:
            session.execute(
                """INSERT INTO intraday_snapshots
                   (stock_id, snapshot_time, price, volume, bid, ask, change_amt, change_pct)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (stock_id, snapshot_time) DO UPDATE SET
                     price = CASE WHEN EXCLUDED.price IS NOT NULL THEN EXCLUDED.price ELSE intraday_snapshots.price END,
                     volume = CASE WHEN EXCLUDED.volume IS NOT NULL THEN EXCLUDED.volume ELSE intraday_snapshots.volume END,
                     bid = CASE WHEN EXCLUDED.bid IS NOT NULL THEN EXCLUDED.bid ELSE intraday_snapshots.bid END,
                     ask = CASE WHEN EXCLUDED.ask IS NOT NULL THEN EXCLUDED.ask ELSE intraday_snapshots.ask END,
                     change_amt = CASE WHEN EXCLUDED.change_amt IS NOT NULL THEN EXCLUDED.change_amt ELSE intraday_snapshots.change_amt END,
                     change_pct = CASE WHEN EXCLUDED.change_pct IS NOT NULL THEN EXCLUDED.change_pct ELSE intraday_snapshots.change_pct END""",
                [r[0], now, r[1], r[2], r[3], r[4], r[5], r[6]],
            )
            snapshot_count += 1
        session.commit()

        cutoff_date = date.today() - timedelta(days=5)
        session.execute(
            "DELETE FROM intraday_snapshots WHERE snapshot_time < ?",
            [cutoff_date],
        )
        session.commit()

        log.info("mis.snapshot_saved", count=snapshot_count, time=now.isoformat())
        return {"status": "ok", "count": snapshot_count}
    except Exception as exc:
        session.rollback()
        log.error("mis.snapshot_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        session.close()


def build_intraday_kline(stock_ids: Optional[list[str]] = None) -> dict[str, Any]:
    """Aggregate realtime_quotes into 60-min intraday K-lines.

    Uses the realtime_quotes table sampled every ~60s to build OHLC candles
    aligned to hour boundaries (09:00, 10:00, 11:00, 12:00, 13:00).
    The ongoing (current) candle is updated in-place.
    """
    if not is_market_open():
        return {"status": "skipped", "reason": "market closed"}

    from tw_quant_selector.data.database import get_session

    today = date.today()
    now = datetime.now()
    current_hour_start = now.replace(minute=0, second=0, microsecond=0)

    # Only build candles for whole hours that have passed
    hour_boundaries: list[datetime] = []
    for h in range(9, 14):  # 09:00, 10:00, 11:00, 12:00, 13:00
        boundary = datetime(today.year, today.month, today.day, h, 0, 0)
        if boundary <= now:
            hour_boundaries.append(boundary)

    session = get_session()
    try:
        kline_count = 0
        for stock_id in stock_ids or []:
            for boundary in hour_boundaries:
                if boundary == current_hour_start:
                    # Ongoing candle: use all quotes from this hour
                    end_time = now
                else:
                    # Completed candle: full hour range
                    end_time = boundary + timedelta(hours=1)

                rows = session.execute(
                    """SELECT price, volume, open_price, high_price, low_price
                       FROM realtime_quotes
                       WHERE stock_id = ? AND quote_time >= ? AND quote_time < ?
                       ORDER BY quote_time ASC""",
                    [stock_id, boundary, end_time],
                ).fetchall()

                if not rows:
                    continue

                prices = [r[0] for r in rows if r[0] is not None]
                if not prices:
                    continue

                open_val = prices[0]
                close_val = prices[-1]
                high_val = max(prices)
                low_val = min(prices)
                vol_start = rows[0][1] or 0
                vol_end = rows[-1][1] or 0
                vol = max(0, vol_end - vol_start)

                session.execute(
                    """INSERT INTO intraday_kline (stock_id, k_time, period_min, open, high, low, close, volume)
                       VALUES (?, ?, 60, ?, ?, ?, ?, ?)
                       ON CONFLICT (stock_id, k_time, period_min)
                       DO UPDATE SET open = EXCLUDED.open, high = EXCLUDED.high,
                                     low = EXCLUDED.low, close = EXCLUDED.close,
                                     volume = EXCLUDED.volume""",
                    [stock_id, boundary, open_val, high_val, low_val, close_val, vol],
                )
                kline_count += 1

        session.commit()
        return {"status": "ok", "count": kline_count}
    except Exception as exc:
        session.rollback()
        log.error("kline.build_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        session.close()


def close_market_prices(db) -> dict[str, Any]:
    now = datetime.now()
    t = now.time()
    if t < MARKET_CLOSE:
        return {"status": "skipped", "reason": "market still open"}

    from tw_quant_selector.data.database import get_session

    session = get_session()
    try:
        session.execute(
            """UPDATE realtime_quotes SET is_close = TRUE
               WHERE quote_time = (
                   SELECT MAX(quote_time) FROM realtime_quotes
               )"""
        )
        session.commit()
        return {"status": "ok"}
    except Exception as exc:
        session.rollback()
        log.error("mis.close_failed", error=str(exc))
        return {"status": "error", "error": str(exc)}
    finally:
        session.close()
