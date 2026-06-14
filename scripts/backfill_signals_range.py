#!/usr/bin/env python3
"""Backfill signals for a specific range of trading days.

Usage:
    PYTHONPATH=src:. .venv/bin/python scripts/backfill_signals_range.py [--start DATE] [--end DATE]
    Default: --start 2026-06-08 --end 2026-06-12 (last week Mon-Fri)
"""
import sys
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tw_quant_selector.data.database import Database
from tw_quant_selector.strategies.combiner import compute_composite_scores, DEFAULT_WEIGHTS
import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
)

db = Database(read_only=False)
db.init_db()


def get_trading_days(start: date, end: date) -> list[date]:
    """Fetch distinct trading dates from daily_prices within the range."""
    rows = db.execute(
        "SELECT DISTINCT trade_date FROM daily_prices "
        "WHERE trade_date >= ? AND trade_date <= ? "
        "ORDER BY trade_date",
        [start, end],
    ).fetchall()
    return [date.fromisoformat(str(r[0])) for r in rows]


def backfill_date(target_date: date) -> bool:
    """Run compute_composite_scores for a single date."""
    print(f"\n{'─'*50}")
    print(f"🔄 生成 {target_date} 的因子信號...")

    try:
        result = compute_composite_scores(
            db,
            as_of_date=target_date,
            weights=DEFAULT_WEIGHTS,
            top_n_stocks=50,
            top_n_etfs=10,
        )
        stocks = len(result.get("stocks", []))
        etfs = len(result.get("etfs", []))
        candidates = result["total_candidates"]
        print(f"  ✅ 完成 — stocks={stocks}, etfs={etfs}, candidates={candidates}")
        return True
    except Exception as e:
        print(f"  ❌ 失敗: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill signals for a date range")
    parser.add_argument("--start", default="2026-06-08", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-06-12", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    trading_days = get_trading_days(start, end)
    if not trading_days:
        print(f"❌ {start} ~ {end} 之間沒有交易日（daily_prices 無資料）")
        db.close()
        sys.exit(1)

    print("=" * 60)
    print(f"📅 Backfill signals: {start} ~ {end}")
    print(f"   交易日數: {len(trading_days)}")
    for d in trading_days:
        print(f"     {d}")
    print("=" * 60)

    success = 0
    failed = 0
    for d in trading_days:
        if backfill_date(d):
            success += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"🏁 完成 — 成功 {success}, 失敗 {failed}")
    print(f"{'='*60}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()