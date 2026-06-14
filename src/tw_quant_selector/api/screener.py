from __future__ import annotations
import structlog
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text as sa_text

from tw_quant_selector.data.database import Database

log = structlog.get_logger()

_cache: Optional[dict[str, dict]] = None
_cache_time: Optional[datetime] = None
_latest_date: Optional[str] = None

_TODAY = "2026-06-08"
_CURRENT_YEAR = 2026


def _get_data_from_db() -> tuple[dict[str, dict], datetime]:
    """Query live from existing tables and return per-stock screener data."""
    db = Database(read_only=True)

    # 1. Latest valuations date (for PE/PB/DY sanity)
    val_date_row = db.execute(
        "SELECT MAX(trade_date) FROM valuations"
    ).fetchone()
    latest_val_date = val_date_row[0] if val_date_row and val_date_row[0] else None
    if latest_val_date:
        latest_val_date = str(latest_val_date)

    # 2. Get latest PE, PB, dividend_yield per stock
    val_rows = db.execute(sa_text(
        "SELECT stock_id, pe_ratio, pb_ratio, dividend_yield "
        "FROM valuations WHERE trade_date = :d"
    ), {"d": latest_val_date}).fetchall()

    val_map: dict[str, dict] = {}
    for r in val_rows:
        sid = r[0]
        if sid:
            val_map[sid] = {
                "pe": float(r[1]) if r[1] is not None else None,
                "pb": float(r[2]) if r[2] is not None else None,
                "dy": float(r[3]) if r[3] is not None else None,
            }

    # 3. Get TTM EPS (sum of latest 4 quarters' EPS per stock)
    ep_rows = db.execute(sa_text("""
        WITH latest_q AS (
            SELECT DISTINCT year_quarter FROM financials
            ORDER BY year_quarter DESC LIMIT 4
        )
        SELECT f.stock_id, SUM(f.eps) AS ttm_eps
        FROM financials f
        WHERE f.year_quarter IN (SELECT year_quarter FROM latest_q)
          AND f.eps IS NOT NULL
        GROUP BY f.stock_id
    """)).fetchall()

    eps_map: dict[str, float] = {}
    for r in ep_rows:
        sid = r[0]
        val = r[1]
        if sid and val is not None:
            eps_map[sid] = float(val)

    # 4. Get latest ROE per stock
    roe_rows = db.execute(sa_text("""
        WITH ranked AS (
            SELECT stock_id, roe, year_quarter,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY year_quarter DESC) AS rn
            FROM financials WHERE roe IS NOT NULL
        )
        SELECT stock_id, roe FROM ranked WHERE rn = 1
    """)).fetchall()

    roe_map: dict[str, float] = {}
    for r in roe_rows:
        sid = r[0]
        val = r[1]
        if sid and val is not None:
            roe_map[sid] = float(val)

    # 5. Get CAGR + price from existing stock_cagr_cache
    cagr_rows = db.execute(sa_text(
        "SELECT stock_id, cagr_1y, price FROM stock_cagr_cache"
    )).fetchall()

    cagr_map: dict[str, dict] = {}
    for r in cagr_rows:
        sid = r[0]
        if sid:
            cagr_map[sid] = {
                "cagr_1y": float(r[1]) if r[1] is not None else None,
                "price": float(r[2]) if r[2] is not None else None,
            }

    # 6. Get stock names & industry from stocks table (for the frontend)
    stock_rows = db.execute(sa_text(
        "SELECT stock_id, stock_name, industry, market FROM stocks"
    )).fetchall()

    info_map: dict[str, dict] = {}
    for r in stock_rows:
        sid = r[0]
        if sid:
            info_map[sid] = {
                "name": r[1] or sid,
                "industry": r[2] or "",
                "market": "tpex" if (r[3] or "").upper() == "OTC" else "twse",
            }

    # 7. Merge into final result
    all_stocks: set[str] = set()
    all_stocks.update(val_map.keys())
    all_stocks.update(eps_map.keys())
    all_stocks.update(roe_map.keys())
    all_stocks.update(cagr_map.keys())

    result: dict[str, dict] = {}
    for sid in sorted(all_stocks):
        v = val_map.get(sid, {})
        c = cagr_map.get(sid, {})
        i = info_map.get(sid, {})
        result[sid] = {
            "price": c.get("price"),
            "cagr_1y": c.get("cagr_1y"),
            "pe": v.get("pe"),
            "pb": v.get("pb"),
            "dy": round(float(v["dy"]) * 100, 2) if v.get("dy") is not None else None,
            "eps": eps_map.get(sid),
            "roe": round(float(roe_map[sid]) * 100, 2) if sid in roe_map and roe_map[sid] is not None else None,
            "fill_days": None,  # TODO: compute from dividend data
            "name": i.get("name", sid),
            "industry": i.get("industry", ""),
            "market": i.get("market", "twse"),
        }

    cached_at = datetime.now(timezone.utc)
    return result, cached_at, latest_val_date


def get_data() -> tuple[Optional[dict], Optional[datetime], Optional[str], bool]:
    global _cache, _cache_time, _latest_date

    if _cache is not None:
        return _cache, _cache_time, _latest_date, False

    try:
        cache, ts, ld = _get_data_from_db()
        if cache:
            _cache = cache
            _cache_time = ts
            _latest_date = ld
            return _cache, _cache_time, _latest_date, False
    except Exception as e:
        log.error("screener.db_error", error=str(e))

    return _cache, _cache_time, _latest_date, False


def warm_cache():
    """Warm the cache on app startup (called from lifespan)."""
    try:
        data, ts, ld = _get_data_from_db()
        global _cache, _cache_time, _latest_date
        _cache = data
        _cache_time = ts
        _latest_date = ld
        log.info("screener.warm_cache", stocks=len(data) if data else 0)
    except Exception as e:
        log.error("screener.warm_error", error=str(e))
