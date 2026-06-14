#!/usr/bin/env python3
"""Backfill daily_prices for a date range using TWSE per-stock STOCK_DAY API.

TWSE STOCK_DAY_ALL (openapi) only returns the latest day. This script uses
www.twse.com.tw per-stock endpoint for specific months.

Endpoint: GET https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=YYYYMMDD&stockNo=CODE

The date=YYYYMMDD parameter selects the MONTH view — the API returns all trading
days in that month. We parse out only the days we need.
"""
import sys
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tw_quant_selector.data.database import Database

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(20),
)

TWSE_WEB = "https://www.twse.com.tw"
MAX_WORKERS = 8
BATCH_REPORT_EVERY = 200

log = structlog.get_logger()


def _safe_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        # Remove commas and +/- prefix
        s = str(v).replace(",", "").replace("+", "").replace("X", "")
        return float(s)
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _roc_to_ad(roc_str: str) -> str:
    """115/06/12 → 2026-06-12"""
    parts = roc_str.split("/")
    year = int(parts[0]) + 1911
    return f"{year:04d}-{parts[1]}-{parts[2]}"


def fetch_twse_stock_month(stock_id: str, query_month: str) -> dict[str, dict] | None:
    """Fetch a single stock's entire month of OHLCV from TWSE.

    query_month: YYYYMMDD (only YYYYMM matters — TWSE returns the whole month)

    Returns dict mapping trade_date (YYYY-MM-DD) → OHLCV dict, or None on failure.
    """
    url = f"{TWSE_WEB}/exchangeReport/STOCK_DAY"
    params = {"response": "json", "date": query_month, "stockNo": stock_id}
    try:
        resp = httpx.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("stat") != "OK" or not data.get("data"):
            return None

        results = {}
        for row in data["data"]:
            # row: [日期, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數]
            roc_date = row[0]
            trade_date = _roc_to_ad(roc_date)
            results[trade_date] = {
                "stock_id": stock_id,
                "trade_date": trade_date,
                "open": _safe_float(row[3]),
                "high": _safe_float(row[4]),
                "low": _safe_float(row[5]),
                "close": _safe_float(row[6]),
                "volume": _safe_int(row[1]),
                "amount": _safe_int(row[2].replace(",", "")),
            }
        return results
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return None
        log.warning("twse.fetch_error", stock_id=stock_id, month=query_month, status=e.response.status_code)
        return None
    except Exception as e:
        log.warning("twse.fetch_error", stock_id=stock_id, month=query_month, error=str(e))
        return None


def upsert_prices(db: Database, rows: list[dict]) -> int:
    """Batch upsert into daily_prices. Returns rows written."""
    if not rows:
        return 0
    n = 0
    for r in rows:
        try:
            with db.connection() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM daily_prices WHERE stock_id = ? AND trade_date = ?",
                    [r["stock_id"], r["trade_date"]],
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE daily_prices SET open=?, high=?, low=?, close=?, volume=?, amount=? "
                        "WHERE stock_id=? AND trade_date=?",
                        [r["open"], r["high"], r["low"], r["close"], r["volume"], r["amount"],
                         r["stock_id"], r["trade_date"]],
                    )
                else:
                    conn.execute(
                        "INSERT INTO daily_prices (stock_id, trade_date, open, high, low, close, volume, amount) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        [r["stock_id"], r["trade_date"], r["open"], r["high"], r["low"], r["close"], r["volume"], r["amount"]],
                    )
                conn.commit()
            n += 1
        except Exception as e:
            log.warning("upsert_error", stock_id=r["stock_id"], date=r["trade_date"], error=str(e))
    return n


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill daily_prices for date range via TWSE per-stock API")
    parser.add_argument("--start", default="2026-06-08", help="Start (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-06-12", help="End (YYYY-MM-DD)")
    parser.add_argument("--stocks", default=None, help="Comma-separated stock IDs (default: all stocks in DB)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help="Concurrent workers")
    args = parser.parse_args()

    db = Database()
    db.init_db()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    # Get stock list
    if args.stocks:
        stock_ids = [s.strip() for s in args.stocks.split(",") if s.strip()]
    else:
        rows = db.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
        stock_ids = [r[0] for r in rows]
    print(f"📋 {len(stock_ids)} stocks to fetch")

    # We need all trading days in the range
    dates_needed = set()
    d = start
    while d <= end:
        dates_needed.add(d.isoformat())
        d += timedelta(days=1)
    print(f"📅 Need prices for {len(dates_needed)} dates: {start} ~ {end}")

    # Determine which months to query (TWSE API returns monthly data)
    months = sorted({(start.year, start.month), (end.year, end.month)})
    month_strs = [f"{y:04d}{m:02d}01" for y, m in months]
    print(f"📆 Will query {len(month_strs)} month(s): {month_strs}")

    # Fetch: for each month, fetch all stocks. Each stock returns the whole month.
    total_rows = []
    total_fetched = 0

    for month_str in month_strs:
        print(f"\n{'─'*50}")
        print(f"🔄 Querying {month_str} ...")

        month_data: dict[str, list[dict]] = {}  # date → rows

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(fetch_twse_stock_month, sid, month_str): sid for sid in stock_ids}
            done = 0
            for fut in as_completed(futures):
                done += 1
                result = fut.result()
                if result:
                    for trade_date, row in result.items():
                        if trade_date in dates_needed:
                            month_data.setdefault(trade_date, []).append(row)
                if done % BATCH_REPORT_EVERY == 0:
                    print(f"  ... {done}/{len(stock_ids)} stocks done")

        # Upsert date by date
        print(f"\n  💾 Upserting...")
        for trade_date in sorted(month_data.keys()):
            rows = month_data[trade_date]
            n = upsert_prices(db, rows)
            total_rows.extend(rows)
            total_fetched += n
            print(f"     {trade_date}: {n} rows")

    print(f"\n{'='*60}")
    print(f"🏁 Done — {total_fetched} total rows upserted across {len(month_strs)} month(s)")
    print(f"{'='*60}")
    db.close()


if __name__ == "__main__":
    main()