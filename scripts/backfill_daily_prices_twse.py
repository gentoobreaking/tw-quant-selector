#!/usr/bin/env python3
"""Backfill daily_prices using TWSE per-stock API, with rate-limit handling.

- Auto-resume: skips stocks already in daily_prices
- Rate-limit: waits + retries on 403/428, backs off on repeated failures
- Batch upsert for speed
- Ctrl+C safe, re-run picks up where it left off
"""
import sys
import os
import time
from datetime import date, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tw_quant_selector.data.database import Database

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(30))  # WARNING+

TWSE_WEB = "https://www.twse.com.tw"
BATCH_SIZE = 12                    # stocks per batch
MAX_WORKERS_PER_MONTH = 1          # one at a time — safest for TWSE rate limits
WAIT_SEC = 120                     # wait between batches
START_DATE = "2025-10-06"
END_DATE = "2026-06-15"
MAX_RETRIES = 999           # effectively infinite — keep retrying
RETRY_WAIT_SEC = 60         # fixed 60s wait on rate limit

log = structlog.get_logger()


def _safe_float(v) -> float | None:
    if v is None or v == "": return None
    try:
        return float(str(v).replace(",", "").replace("+", "").replace("X", ""))
    except (ValueError, TypeError):
        return None


def _safe_int(v) -> int | None:
    if v is None or v == "": return None
    try:
        return int(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _roc_to_ad(roc_str: str) -> str:
    parts = roc_str.split("/")
    return f"{int(parts[0]) + 1911:04d}-{parts[1]}-{parts[2]}"


def fetch_twse_stock_month(stock_id: str, query_month: str) -> dict[str, dict] | None:
    """Fetch one stock's month from TWSE, with retries on rate limit."""
    url = f"{TWSE_WEB}/exchangeReport/STOCK_DAY"
    params = {"response": "json", "date": query_month, "stockNo": stock_id}

    for attempt in range(MAX_RETRIES):
        try:
            resp = httpx.get(url, params=params, timeout=15)

            # ── rate-limited → wait and retry ──
            if resp.status_code in (403, 428, 429):
                print(f"     ⚠️  {stock_id} rate-limited ({resp.status_code}), waiting {RETRY_WAIT_SEC}s (attempt {attempt+1})")
                time.sleep(RETRY_WAIT_SEC)
                continue

            resp.raise_for_status()
            data = resp.json()
            if data.get("stat") != "OK" or not data.get("data"):
                return None

            results = {}
            for row in data["data"]:
                trade_date = _roc_to_ad(row[0])
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
            if e.response.status_code in (403, 428, 429):
                print(f"     ⚠️  {stock_id} rate-limited ({e.response.status_code}), waiting {RETRY_WAIT_SEC}s (attempt {attempt+1})")
                time.sleep(RETRY_WAIT_SEC)
                continue
            if attempt == MAX_RETRIES - 1:
                return None
            time.sleep(2)
        except Exception:
            if attempt == MAX_RETRIES - 1:
                return None
            time.sleep(2)

    return None


def upsert_prices_batch(db: Database, rows: list[dict]) -> int:
    """Batch upsert into daily_prices using a single connection + many VALUES."""
    if not rows:
        return 0

    # Deduplicate within this batch: last write wins
    seen = {}
    for r in rows:
        key = (r["stock_id"], r["trade_date"])
        seen[key] = r
    deduped = list(seen.values())

    n = 0
    with db.connection() as conn:
        for r in deduped:
            try:
                conn.execute(
                    "INSERT INTO daily_prices (stock_id, trade_date, open, high, low, close, volume, amount) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT (stock_id, trade_date) DO UPDATE SET "
                    "open=excluded.open, high=excluded.high, low=excluded.low, close=excluded.close, "
                    "volume=excluded.volume, amount=excluded.amount",
                    [r["stock_id"], r["trade_date"], r["open"], r["high"], r["low"], r["close"], r["volume"], r["amount"]],
                )
                n += 1
            except Exception:
                pass
        conn.commit()

    return n


def get_missing(db: Database) -> list[str]:
    rows = db.execute("""
        SELECT s.stock_id FROM stocks s
        WHERE s.stock_id NOT IN (SELECT DISTINCT stock_id FROM daily_prices)
        ORDER BY s.stock_id
    """).fetchall()
    return [r[0] for r in rows]


def get_month_strs(start: date, end: date) -> list[str]:
    months = set()
    d = start
    while d <= end:
        months.add((d.year, d.month))
        d += timedelta(days=1)
    return sorted(f"{y:04d}{m:02d}04" for y, m in months)  # day=04 more reliable than 01


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill daily_prices via TWSE with rate-limit handling")
    parser.add_argument("--start", default=START_DATE, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default=END_DATE, help="End date (YYYY-MM-DD)")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE, help="Stocks per batch")
    parser.add_argument("--wait", type=int, default=WAIT_SEC, help="Seconds between batches")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_PER_MONTH, help="Concurrent month requests")
    args = parser.parse_args()

    db = Database()
    db.init_db()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    month_strs = get_month_strs(start, end)
    dates_needed = set()
    d = start
    while d <= end:
        dates_needed.add(d.isoformat())
        d += timedelta(days=1)

    # ── Auto-resume ──
    missing = get_missing(db)
    if not missing:
        print("✅ All stocks already have daily_prices.")
        db.close()
        return

    total_batches = (len(missing) + args.batch - 1) // args.batch
    print(f"TWSE daily_prices backfill")
    print(f"  Range:    {start} → {end}  ({len(dates_needed)} days)")
    print(f"  Months:   {month_strs}")
    print(f"  Missing:  {len(missing)} stocks  ({total_batches} batches of {args.batch})")
    print(f"  Workers:  {args.workers}/month,  wait={args.wait}s/batch")
    print()

    for i in range(0, len(missing), args.batch):
        batch = missing[i:i + args.batch]
        batch_num = i // args.batch + 1

        # Skip stocks that were already filled
        with db.connection() as conn:
            already = {r[0] for r in conn.execute(
                "SELECT DISTINCT stock_id FROM daily_prices WHERE stock_id = ANY(?)",
                [batch],
            ).fetchall()}
        batch = [s for s in batch if s not in already]
        if not batch:
            print(f"[{batch_num}/{total_batches}] all already filled, skip")
            continue

        preview = ", ".join(batch[:5]) + ("..." if len(batch) > 5 else "")
        print(f"[{batch_num}/{total_batches}] {len(batch)} stocks: {preview}")

        all_rows = []
        for month_str in month_strs:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                futures = {pool.submit(fetch_twse_stock_month, sid, month_str): sid for sid in batch}
                for fut in as_completed(futures):
                    result = fut.result()
                    if result:
                        for td, row in result.items():
                            if td in dates_needed:
                                all_rows.append(row)

        if all_rows:
            n = upsert_prices_batch(db, all_rows)
            print(f"  → {n} rows written")
        else:
            print(f"  → no data (TWSE may have rejected all requests)")

        # Progress
        remaining = get_missing(db)
        pct = (len(missing) - len(remaining)) * 100 // max(len(missing), 1)
        eta_batches = (len(remaining) + args.batch - 1) // args.batch
        eta_min = eta_batches * (args.wait // 60 + 1)
        print(f"  Progress: {pct}%  ({len(remaining)} left, ~{eta_min} min ETA)")

        if i + args.batch < len(missing) and remaining:
            print(f"  Waiting {args.wait}s ...")
            try:
                time.sleep(args.wait)
            except KeyboardInterrupt:
                print("\n\nInterrupted. Progress saved. Re-run to resume.")
                db.close()
                sys.exit(0)

    print(f"\nDone — {len(get_missing(db))} stocks remain (may have no TWSE listing)")
    db.close()


if __name__ == "__main__":
    main()