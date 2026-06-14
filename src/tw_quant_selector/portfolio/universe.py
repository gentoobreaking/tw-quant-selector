from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
import pandas as pd
import structlog
from sqlalchemy import text

log = structlog.get_logger()

ETF_LIST: list[dict[str, str]] = [
    {"stock_id": "0050", "name": "元大台灣50"},
    {"stock_id": "0051", "name": "元大中型100"},
    {"stock_id": "0052", "name": "富邦科技"},
    {"stock_id": "0056", "name": "元大高股息"},
    {"stock_id": "00878", "name": "國泰永續高股息"},
    {"stock_id": "00881", "name": "國泰台灣5G+"},
    {"stock_id": "006208", "name": "富邦台50"},
]

ETF_IDS = {e["stock_id"] for e in ETF_LIST}
SUSPEND_THRESHOLD_DAYS = 30
REENTRY_OBSERVE_DAYS = 5


def get_universe(db, as_of_date: date) -> dict[str, Any]:
    with db.connection() as conn:
        stocks = pd.DataFrame(
            conn.execute(text("""SELECT stock_id, stock_name, market, industry, list_date, delist_date
               FROM stocks WHERE is_etf = FALSE""")).fetchall(),
            columns=["stock_id", "stock_name", "market", "industry", "list_date", "delist_date"])

    etfs = _get_etf_universe(db, as_of_date, stocks)
    universe = _filter_static(db, as_of_date, stocks)
    universe = _filter_dynamic(db, as_of_date, universe)
    universe = _filter_delisted(universe, as_of_date)
    universe = _filter_suspended(db, as_of_date, universe)

    return {
        "stocks": universe,
        "etfs": etfs,
        "total_count": len(universe) + len(etfs),
        "as_of_date": as_of_date.isoformat(),
    }


def _get_etf_universe(db, as_of_date: date, stocks_df) -> list[dict]:
    return [{"stock_id": e["stock_id"], "stock_name": e["name"], "market": "TSE", "is_etf": True}
            for e in ETF_LIST]


def _filter_static(db, as_of_date: date, stocks_df):
    month_start = as_of_date.replace(day=1)
    use_date = db.execute(
        text("""SELECT COALESCE(
            (SELECT MIN(trade_date) FROM valuations WHERE trade_date >= :month_start),
            (SELECT MAX(trade_date) FROM valuations WHERE trade_date < :month_start),
            (SELECT MAX(trade_date) FROM valuations)
        )"""),
        {"month_start": month_start}
    ).fetchone()[0]
    if use_date is None:
        return []
    vals = pd.DataFrame(
        db.execute(text("""SELECT v.stock_id, v.market_cap, s.industry, s.stock_name
           FROM valuations v JOIN stocks s ON v.stock_id = s.stock_id
           WHERE (v.stock_id, v.trade_date) IN (
               SELECT stock_id, MAX(trade_date) FROM valuations WHERE trade_date <= :use_date GROUP BY stock_id
           )"""), {"use_date": use_date}).fetchall(),
        columns=["stock_id", "market_cap", "industry", "stock_name"])

    excluded_industries = {"金融保險", "金融及其他", "金融"}
    excluded_keywords = {"-KY", "ＫＹ"}

    candidates = []
    for _, row in vals.replace({pd.NA: None}).iterrows():
        sid = row["stock_id"]
        ind = str(row.get("industry") or "").strip()
        name = str(row.get("stock_name") or "").upper()
        if ind in excluded_industries:
            continue
        if any(kw in name for kw in excluded_keywords):
            continue
        cap = row.get("market_cap")
        if cap is not None and not (isinstance(cap, float) and (cap != cap)):
            try:
                if cap < 3000000000:
                    continue
            except TypeError:
                continue
        candidates.append(sid)
    return [s for s in stocks_df.to_dict("records") if s["stock_id"] in set(candidates)]


def _filter_dynamic(db, as_of_date: date, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    start_20d = as_of_date - timedelta(days=40)
    sids = [c["stock_id"] for c in candidates]
    result = []
    for sid in sids:
        rows = pd.DataFrame(
            db.execute(text("""SELECT amount FROM daily_prices
               WHERE stock_id = :sid AND trade_date >= :start_20d AND trade_date < :as_of_date
               ORDER BY trade_date DESC LIMIT 20"""),
                {"sid": sid, "start_20d": start_20d, "as_of_date": as_of_date}).fetchall(),
            columns=["amount"])
        if rows.empty:
            continue
        avg_amount = float(rows["amount"].astype(float).mean())
        if avg_amount and avg_amount >= 10_000_000:
            result.append(sid)
    return [c for c in candidates if c["stock_id"] in set(result)]


def _filter_delisted(candidates: list[dict], as_of_date: date) -> list[dict]:
    result = []
    for c in candidates:
        d = c.get("delist_date")
        if d is None or str(d) == "NaT" or d > as_of_date:
            result.append(c)
    return result


def _filter_suspended(db, as_of_date: date, candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    sustained = []
    for c in candidates:
        sid = c["stock_id"]
        recent = pd.DataFrame(
            db.execute(text("""SELECT trade_date, volume FROM daily_prices
               WHERE stock_id = :sid AND trade_date < :as_of_date
               ORDER BY trade_date DESC LIMIT :limit"""),
                {"sid": sid, "as_of_date": as_of_date, "limit": SUSPEND_THRESHOLD_DAYS + 5}).fetchall(),
            columns=["trade_date", "volume"])
        if recent.empty:
            sustained.append(c)
            continue
        zero_vol_days = 0
        for _, row in recent.iterrows():
            if row["volume"] is None or row["volume"] == 0:
                zero_vol_days += 1
            else:
                if zero_vol_days >= SUSPEND_THRESHOLD_DAYS:
                    break
                zero_vol_days = 0
        if zero_vol_days >= SUSPEND_THRESHOLD_DAYS:
            log.info("universe.suspended.removed", stock_id=sid, days=zero_vol_days)
            continue
        sustained.append(c)
    return sustained
