#!/usr/bin/env python3
"""
Check the status of staggered ingestion: coverage per dataset, buckets, ETA.
"""
import os, sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tw_quant_selector.data.database import Database
from tw_quant_selector.data.scheduler import _init_tracker, STOCKS_PER_DAY, DATASETS

db = Database()

bucket_counts = _init_tracker(db, date.today())
total_buckets = len(bucket_counts)

with db.connection() as conn:
    total_stocks = conn.execute("SELECT COUNT(*) FROM stocks").fetchone()[0]
    total_etfs = conn.execute("SELECT COUNT(*) FROM stocks WHERE is_etf = TRUE").fetchone()[0]

    print(f"📊 Ingestion Status")
    print(f"  Total stocks: {total_stocks} (incl. {total_etfs} ETFs)")
    print(f"  Buckets:      {total_buckets} (~{STOCKS_PER_DAY} stocks/day)")
    if total_buckets > 0:
        today_bucket = date.today().toordinal() % total_buckets
        print(f"  Today bucket: {today_bucket}")
        print(f"  Full cycle:   {total_buckets} days")
        next_bucket = (today_bucket + 1) % total_buckets
        print(f"  ETA for all fresh: {total_buckets - 1} more days (bucket {next_bucket} tomorrow)")

    print()
    for ds in DATASETS:
        ok = conn.execute(
            "SELECT COUNT(*) FROM ingestion_tracker WHERE dataset = ? AND last_status = 'ok'",
            [ds]
        ).fetchone()[0]
        failed = conn.execute(
            "SELECT COUNT(*) FROM ingestion_tracker WHERE dataset = ? AND last_status = 'failed'",
            [ds]
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM ingestion_tracker WHERE dataset = ? AND last_updated IS NULL",
            [ds]
        ).fetchone()[0]
        print(f"  {ds:15s}: ✅ {ok}  ❌ {failed}  ⏳ {pending}")

    last_update = conn.execute(
        "SELECT MAX(last_updated) FROM ingestion_tracker"
    ).fetchone()[0]
    if last_update:
        print(f"\n  Last data update: {last_update}")

db.close()
