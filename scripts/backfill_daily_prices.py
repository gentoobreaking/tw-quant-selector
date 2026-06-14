"""
一次性歷史股價回補：用 FinMind 為所有（或指定）股票拉 N 天日線。

－ 不刪除既有 TWSE 資料
－ 直接用 FinMind client + _upsert，繞過 _filter_stocks_needing_update

用法：
  FINMIND_TOKEN=xxx python scripts/backfill_daily_prices.py

可選參數：
  --lookback-days 252    回溯天數（預設 252）
  --batch-size 10        每批幾檔（FinMind free tier 5/min，建議 5~10）
  --wait-seconds 180     批次間等待秒數（預設 180 秒 = 3 分鐘）
  --stock-ids "2330,0050" 只補指定股票
  --skip-existing        已存在 >= lookback_days 天資料的 stock 跳過
  --resume               從上次中斷處續跑（讀取 ./backfill_progress.json）
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tw_quant_selector.data.database import Database
from tw_quant_selector.data.finmind_client import FinMindClient, FinMindRateLimitError
from tw_quant_selector.data.ingestion import _upsert, _clean_nan

# FinMind → DB column mapping
FINMIND_COLS = {
    "stock_id": "stock_id",
    "date": "trade_date",
    "open": "open",
    "max": "high",
    "min": "low",
    "close": "close",
    "Trading_Volume": "volume",
    "Trading_money": "amount",
}

PROGRESS_FILE = Path(__file__).resolve().parent.parent / ".backfill_progress.json"

parser = argparse.ArgumentParser(description="一次性回補歷史股價")
parser.add_argument("--lookback-days", type=int, default=252, help="回溯天數（預設 252）")
parser.add_argument("--batch-size", type=int, default=10, help="每批處理檔數")
parser.add_argument("--wait-seconds", type=int, default=180, help="批次間等待秒數")
parser.add_argument("--stock-ids", type=str, help="逗號分隔的股票代號（不給則全部）")
parser.add_argument("--skip-existing", action="store_true",
                    help=f"已有 >= --lookback-days 天日線的 stock 跳過")
parser.add_argument("--resume", action="store_true", help="從上次中斷處續跑")
parser.add_argument("token", nargs="?", help="FinMind API token（或用 FINMIND_TOKEN env）")
args = parser.parse_args()

token = args.token or os.environ.get("FINMIND_TOKEN", "")
if not token:
    parser.print_help()
    sys.exit(1)

db = Database()
client = FinMindClient(token)

end_date = date.today()
start_date = end_date - timedelta(days=args.lookback_days)

print(f"📅 回補範圍: {start_date} ~ {end_date} ({args.lookback_days} 天)")
print(f"📦 每批: {args.batch_size} 檔, 間隔: {args.wait_seconds}s")

# ── Determine stock list ──
if args.stock_ids:
    all_ids = [s.strip() for s in args.stock_ids.split(",") if s.strip()]
    print(f"🎯 指定標的: {len(all_ids)} 檔")
else:
    with db.connection() as conn:
        rows = conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
    all_ids = [r[0] for r in rows]
    print(f"📋 全部標的: {len(all_ids)} 檔")

# ── Resume support ──
completed: set[str] = set()
failed_sids: set[str] = set()
if args.resume and PROGRESS_FILE.exists():
    state = json.loads(PROGRESS_FILE.read_text())
    completed = set(state.get("completed", []))
    failed_sids = set(state.get("failed", []))
    print(f"📌 續跑: {len(completed)} 已完成, {len(failed_sids)} 失敗 (會被跳過)")
    all_ids = [s for s in all_ids if s not in completed and s not in failed_sids]

if args.skip_existing:
    # 查哪些 stock 的 daily_prices 已經夠多天
    with db.connection() as conn:
        rows = conn.execute("""
            SELECT stock_id, COUNT(*) as cnt
            FROM daily_prices
            WHERE trade_date >= :start AND stock_id = ANY(:ids)
            GROUP BY stock_id
        """, {"start": start_date, "ids": all_ids}).fetchall()
    skip_set = {r[0] for r in rows if r[1] >= args.lookback_days * 0.8}
    skipped = [s for s in all_ids if s in skip_set]
    all_ids = [s for s in all_ids if s not in skip_set]
    print(f"⏭ 跳過 {len(skipped)} 檔（已有 >= {int(args.lookback_days * 0.8)} 天）")

# ── Filter FinMind-valid IDs ──
import re
_finmind_re = re.compile(r"^\d{4}$|^00\d{3,4}$|^\d{5}[A-Z]?$|^\d{4}[A-Z]\d{0,2}$|^\d{6}[A-Z]?$")
valid_ids = [s for s in all_ids if _finmind_re.match(s)]
invalid = len(all_ids) - len(valid_ids)
if invalid:
    print(f"⚠️ 跳過 {invalid} 個非 FinMind 格式 ID")

if not valid_ids:
    print("✅ 沒有需要回補的 stock")
    db.close()
    sys.exit(0)

batches = [valid_ids[i:i + args.batch_size] for i in range(0, len(valid_ids), args.batch_size)]
total_batches = len(batches)

print(f"\n🚀 開始回補 ({len(valid_ids)} 檔, {total_batches} 批)")
print("=" * 60)

total_rows = 0

for batch_idx, batch in enumerate(batches, 1):
    preview = ", ".join(batch[:5]) + ("..." if len(batch) > 5 else "")
    print(f"\n📥 批次 {batch_idx}/{total_batches} ({len(batch)} 檔): {preview}")

    n = 0
    rate_limit_hits = 0
    MAX_RATE_LIMIT_RETRIES = 5

    for sid in batch:
        done = False
        while not done:
            try:
                raw = client.get_daily_prices(sid, start_date, end_date)
                if not raw:
                    completed.add(sid)
                    done = True
                    continue
                rows = [{FINMIND_COLS.get(k, k): _clean_nan(v)
                         for k, v in r.items() if k in FINMIND_COLS}
                        for r in raw]
                if not rows:
                    completed.add(sid)
                    done = True
                    continue
                with db.connection() as conn:
                    _upsert(conn, "daily_prices", rows, ["stock_id", "trade_date"])
                    conn.commit()
                n += len(rows)
                completed.add(sid)
                done = True
            except FinMindRateLimitError as e:
                rate_limit_hits += 1
                print(f"   🛑 {sid} 限流 ({rate_limit_hits}/{MAX_RATE_LIMIT_RETRIES}): {e}")
                if rate_limit_hits >= MAX_RATE_LIMIT_RETRIES:
                    print(f"   已達最大重試次數，存進度並退出")
                    PROGRESS_FILE.write_text(json.dumps({
                        "completed": sorted(completed), "failed": sorted(failed_sids),
                        "last_batch": batch_idx, "total_batches": total_batches,
                    }, ensure_ascii=False, indent=2))
                    print(f"   進度檔: {PROGRESS_FILE}（--resume 續跑）")
                    db.close()
                    sys.exit(75)
                wait_min = 60
                print(f"   ⏳ 自動等待 {wait_min} 分鐘後重試...")
                time.sleep(wait_min * 60)
                # 繼續 while loop，重試同一個 sid
            except Exception as e:
                print(f"   ⚠️ {sid} 失敗: {e}")
                failed_sids.add(sid)
                done = True

    print(f"   ✅ {n:>7} 行寫入")
    total_rows += n

    # 存進度
    PROGRESS_FILE.write_text(json.dumps({
        "completed": sorted(completed), "failed": sorted(failed_sids),
        "last_batch": batch_idx, "total_batches": total_batches,
    }, ensure_ascii=False, indent=2))

    if batch_idx < total_batches:
        print(f"   ⏳ 等待 {args.wait_seconds}s 避免限流...")
        time.sleep(args.wait_seconds)

# Cleanup
if PROGRESS_FILE.exists():
    PROGRESS_FILE.unlink()

print(f"\n{'='*60}")
print(f"✅ 回補完成: {total_rows} 行")
if failed_sids:
    print(f"⚠️ {len(failed_sids)} 檔失敗: {', '.join(sorted(failed_sids)[:10])}...")
print(f"已完成: {len(completed)} 檔")

db.close()