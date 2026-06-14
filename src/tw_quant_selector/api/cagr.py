from __future__ import annotations
import threading
import time
import structlog
from datetime import datetime, timezone, timedelta
from typing import Optional

import yfinance as yf
from yfinance.exceptions import YFRateLimitError
import pandas as pd
from sqlalchemy import text as sa_text

from tw_quant_selector.data.database import Database

log = structlog.get_logger()

_cache: Optional[dict[str, dict]] = None
_cache_time: Optional[datetime] = None
_warming = False

_BATCH_SIZE = 50
_BACKOFF_INITIAL = 10
_BACKOFF_MAX = 120


# ── Table management (raw SQL, avoids ORM naming-convention issues) ──

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_cagr_cache (
    stock_id   VARCHAR(10) PRIMARY KEY,
    cagr_1y    DECIMAL(10, 4),
    price      DECIMAL(12, 2),
    date       DATE NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def _ensure_table():
    db = Database()
    db.execute(_CREATE_TABLE_SQL)


# ── Ticker list ──

def _build_ticker_list() -> list[str]:
    db = Database()
    rows = db.execute(
        "SELECT stock_id, market FROM stocks WHERE is_etf = false AND delist_date IS NULL"
    ).fetchall()
    tickers = []
    for r in rows:
        sid = r[0]
        suffix = ".TWO" if (r[1] or "").upper() == "OTC" else ".TW"
        tickers.append(f"{sid}{suffix}")
    return tickers


def _get_missing_tickers(tickers: list[str], today_str: str) -> list[str]:
    """Return tickers whose DB cache is not from today."""
    _ensure_table()
    db = Database()
    codes = [t.replace(".TW", "").replace(".TWO", "") for t in tickers]

    placeholders = ",".join([f"'{c}'" for c in codes])
    rows = db.execute(
        sa_text(
            f"SELECT stock_id FROM stock_cagr_cache WHERE date = :today AND stock_id IN ({placeholders})"
        ),
        {"today": today_str},
    ).fetchall()
    cached = {r[0] for r in rows}

    missing = []
    for t in tickers:
        code = t.replace(".TW", "").replace(".TWO", "")
        if code not in cached:
            missing.append(t)
    return missing


# ── CAGR computation ──

def _compute_cagr_from_df(adj: pd.DataFrame, end: datetime) -> dict[str, dict]:
    result: dict[str, dict] = {}
    cutoff = end - timedelta(days=365)
    for ticker in adj.columns:
        series = adj[ticker].dropna()
        if len(series) < 2:
            continue
        latest = series.iloc[-1]
        before = series[series.index <= cutoff]
        if before.empty:
            continue
        prev = before.iloc[-1]
        ret_1y = (latest / prev - 1) * 100 if prev > 0 else None
        code = ticker.replace(".TW", "").replace(".TWO", "")
        result[code] = {
            "cagr_1y": round(float(ret_1y), 2) if ret_1y is not None else None,
            "price": round(float(latest), 2),
            "date": str(series.index[-1].date()),
        }
    return result


# ── Yahoo Finance batch fetch ──

def _fetch_batch(batch: list[str], start_str: str, end_str: str) -> pd.DataFrame | None:
    backoff = _BACKOFF_INITIAL
    for attempt in range(5):
        try:
            data = yf.download(
                batch, start=start_str, end=end_str,
                group_by="column", threads=False, progress=False,
            )
            return data.get("Adj Close", data.get("Close"))
        except YFRateLimitError:
            log.warning("cagr.rate_limited", batch_size=len(batch), attempt=attempt + 1, wait=backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX)
        except Exception as e:
            log.warning("cagr.batch_error", batch_size=len(batch), error=str(e))
            return None
    log.warning("cagr.batch_exhausted", batch_size=len(batch))
    return None


# ── DB read / write ──

def _write_to_db(result: dict[str, dict]):
    _ensure_table()
    db = Database()
    today_str = datetime.now().strftime("%Y-%m-%d")
    upsert = sa_text(
        """INSERT INTO stock_cagr_cache (stock_id, cagr_1y, price, date)
           VALUES (:sid, :cagr, :price, :dt)
           ON CONFLICT (stock_id) DO UPDATE SET
               cagr_1y    = EXCLUDED.cagr_1y,
               price      = EXCLUDED.price,
               date       = EXCLUDED.date,
               updated_at = CURRENT_TIMESTAMP"""
    )
    try:
        for code, data in result.items():
            db.execute(upsert, {
                "sid": code,
                "cagr": data["cagr_1y"],
                "price": data.get("price"),
                "dt": today_str,
            })
        log.info("cagr.db_write", count=len(result))
    except Exception as e:
        log.error("cagr.db_write_error", error=str(e))


def _read_from_db() -> tuple[dict[str, dict], datetime]:
    _ensure_table()
    db = Database()
    try:
        rows = db.execute(sa_text(
            "SELECT stock_id, cagr_1y, price, date, updated_at FROM stock_cagr_cache ORDER BY updated_at DESC"
        )).fetchall()
    except Exception:
        return {}, datetime.min
    if not rows:
        return {}, datetime.min

    result = {}
    latest_ts = datetime.min
    for r in rows:
        result[r[0]] = {
            "cagr_1y": float(r[1]) if r[1] is not None else None,
            "price": float(r[2]) if r[2] is not None else None,
            "date": str(r[3]) if r[3] else None,
        }
        if r[4] and r[4] > latest_ts:
            latest_ts = r[4]
    return result, latest_ts


# ── Main fetch logic ──

def _fetch():
    global _cache, _cache_time, _warming
    try:
        all_tickers = _build_ticker_list()
        end = datetime.now()
        today_str = end.strftime("%Y-%m-%d")
        end_str = today_str
        start_str = (end - timedelta(days=370)).strftime("%Y-%m-%d")

        missing = _get_missing_tickers(all_tickers, today_str)
        total_all = len(all_tickers)
        total_missing = len(missing)
        total_cached = total_all - total_missing

        log.info("cagr.fetch_start", count=total_all, cached=total_cached, to_fetch=total_missing)

        if total_missing == 0:
            _cache, _cache_time = _read_from_db()
            _warming = False
            log.info("cagr.fetch_done", stocks=len(_cache), source="db")
            return

        merged: dict[str, dict] = {}
        total_batches = (total_missing + _BATCH_SIZE - 1) // _BATCH_SIZE
        for i in range(0, total_missing, _BATCH_SIZE):
            batch = missing[i : i + _BATCH_SIZE]
            log.info("cagr.batch", batch=i // _BATCH_SIZE + 1, of=total_batches, size=len(batch))

            adj = _fetch_batch(batch, start_str, end_str)
            if adj is not None and not adj.empty:
                merged.update(_compute_cagr_from_df(adj, end))

            if i + _BATCH_SIZE < total_missing:
                time.sleep(2)

        if merged:
            _write_to_db(merged)

        _cache, _cache_time = _read_from_db()
        _warming = False
        log.info("cagr.fetch_done", stocks=len(_cache), source="yfinance+db")
    except Exception as e:
        log.error("cagr.fetch_error", error=str(e))
        _warming = False


# ── Public API ──

def warm_cache():
    global _warming
    if _warming:
        return
    _warming = True
    t = threading.Thread(target=_fetch, daemon=True)
    t.start()


def get_data() -> tuple[Optional[dict], Optional[datetime], bool]:
    if _cache is not None:
        return _cache, _cache_time, _warming
    try:
        cache, ts = _read_from_db()
        if cache:
            return cache, ts, _warming
    except Exception:
        pass
    return _cache, _cache_time, _warming
