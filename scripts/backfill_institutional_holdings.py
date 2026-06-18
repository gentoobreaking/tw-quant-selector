"""
一次性法人持股比率回補：從 FinMind 補 institutional_holdings 歷史資料。

用法：
  docker compose exec app python3 scripts/backfill_institutional_holdings.py

可選參數：
  --lookback-weeks 12     回補最近 N 週（預設 12，每週一個快照）
  --start-date 2026-01-01 指定起始日期（覆蓋 --lookback-weeks）
  --end-date 2026-06-15   指定結束日期（預設今天）
  --batch-size 20         每批幾檔（建議 10~30）
  --wait-seconds 120      批次間等待秒數（建議 60~180）
  --stock-ids "2330,2317" 只補指定股票（不設則全部）
  --resume                從上次中斷處續跑（讀取 .backfill_progress_ih.json）

法人持股每週發布一次，FinMind 快照日期通常為每週最後一個交易日。
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tw_quant_selector.data.database import Database
from tw_quant_selector.data.finmind_client import FinMindClient, FinMindRateLimitError
from tw_quant_selector.data.ingestion import _upsert

PROGRESS_FILE = Path(__file__).resolve().parent.parent / ".backfill_progress_ih.json"

# 法人持股每週一更新一筆，所以只取週一作為快照日
HOLDING_COLUMNS = {
    "date": "snapshot_date",
    "stock_id": "stock_id",
    "ForeignInvestor": "foreign_holding_pct",
    "SITI": "trust_holding_pct",
}


def _monday_of_week(d: date) -> date:
    """取得 d 所屬週的週一。"""
    return d - timedelta(days=d.weekday())


def _generate_snapshot_dates(start: date, end: date) -> list[date]:
    """產生 start ~ end 區間內每週週一的日期清單。"""
    # 從 start 的週一開始
    cur = _monday_of_week(start)
    dates = []
    while cur <= end:
        dates.append(cur)
        cur += timedelta(weeks=1)
    return dates


def _transform_row(raw: dict, stock_id: str, snapshot_date: str) -> dict | None:
    """將 FinMind 回應的一列轉為 institutional_holdings 格式。"""
    row = {}
    for k, v in raw.items():
        target = HOLDING_COLUMNS.get(k)
        if target:
            row[target] = v
    if not row:
        return None
    row.setdefault("snapshot_date", snapshot_date)
    row.setdefault("stock_id", stock_id)

    foreign_pct = row.get("foreign_holding_pct")
    trust_pct = row.get("trust_holding_pct")
    raw_total = (foreign_pct + trust_pct) if (foreign_pct is not None and trust_pct is not None) else None
    row["dealer_holding_pct"] = round(100.0 - raw_total, 4) if raw_total is not None else None
    row["total_inst_pct"] = (
        round(foreign_pct + trust_pct + (row.get("dealer_holding_pct") or 0), 4)
        if foreign_pct is not None
        else None
    )
    row["data_source"] = "finmind"
    return row


def _fetch_for_stock(client: FinMindClient, stock_id: str, snapshot_date: str) -> list[dict]:
    """對單一股票抓取一個日期的持股資料。"""
    try:
        raw = client.get_shareholding(stock_id, snapshot_date, snapshot_date)
    except FinMindRateLimitError:
        raise
    except Exception:
        return []

    if not raw:
        return []

    rows = []
    for r in raw:
        row = _transform_row(r, stock_id, snapshot_date)
        if row:
            rows.append(row)
    return rows


parser = argparse.ArgumentParser(description="法人持股比率一次性回補")
parser.add_argument("--lookback-weeks", type=int, default=12, help="回補最近 N 週（預設 12）")
parser.add_argument("--start-date", type=str, help="起始日期 YYYY-MM-DD（覆蓋 --lookback-weeks）")
parser.add_argument("--end-date", type=str, help="結束日期 YYYY-MM-DD（預設今天）")
parser.add_argument("--batch-size", type=int, default=20, help="每批處理檔數")
parser.add_argument("--wait-seconds", type=int, default=120, help="批次間等待秒數")
parser.add_argument("--stock-ids", type=str, help="逗號分隔的股票代號（不設則全部）")
parser.add_argument("--resume", action="store_true", help="從上次中斷處續跑")
parser.add_argument("token", nargs="?", help="FinMind API token（或用 FINMIND_TOKEN env）")
args = parser.parse_args()

token = args.token or os.environ.get("FINMIND_TOKEN", "")
if not token:
    parser.print_help()
    sys.exit(1)

# ── 日期區間 ──
end_date = date.today()
if args.end_date:
    end_date = date.fromisoformat(args.end_date)

if args.start_date:
    start_date = date.fromisoformat(args.start_date)
else:
    start_date = end_date - timedelta(weeks=args.lookback_weeks)

snap_dates = _generate_snapshot_dates(start_date, end_date)
print(f"📅 回補期間: {start_date} ~ {end_date} ({len(snap_dates)} 個週快照)")
print(f"📦 每批: {args.batch_size} 檔, 間隔: {args.wait_seconds}s")

# ── 股票清單 ──
db = Database()
client = FinMindClient(token)

if args.stock_ids:
    all_ids = [s.strip() for s in args.stock_ids.split(",") if s.strip()]
    print(f"🎯 指定標的: {len(all_ids)} 檔")
else:
    with db.connection() as conn:
        rows = conn.execute("SELECT stock_id FROM stocks ORDER BY stock_id").fetchall()
    all_ids = [r[0] for r in rows]
    print(f"📋 全部上市櫃: {len(all_ids)} 檔")

# ── FinMind 格式驗證 ──
_finmind_re = re.compile(r"^\d{4}$|^00\d{3,4}$|^\d{5}[A-Z]?$|^\d{4}[A-Z]\d{0,2}$|^\d{6}[A-Z]?$")
valid_ids = [s for s in all_ids if _finmind_re.match(s)]
invalid = len(all_ids) - len(valid_ids)
if invalid:
    print(f"⚠️ 跳過 {invalid} 個非 FinMind 格式 ID")

if not valid_ids:
    print("✅ 沒有需要回補的股票")
    db.close()
    sys.exit(0)

# ── 檢查各日期已存在的 stock_id ──
# existing[date] = set of stock_ids already in DB for that date
existing_by_date: dict[str, set[str]] = {}
for sd in snap_dates:
    sd_str = sd.isoformat()
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT stock_id FROM institutional_holdings WHERE snapshot_date = ?",
            [sd_str],
        ).fetchall()
    existing_by_date[sd_str] = {r[0] for r in rows}

total_needed = 0
for sd_str, existing in existing_by_date.items():
    missing = len(valid_ids) - len(existing)
    total_needed += missing
    if missing > 0:
        print(f"  {sd_str}: 已 {len(existing)} 檔, 缺 {missing} 檔")

if total_needed == 0:
    print("✅ 所有快照已齊全，無需回補")
    db.close()
    sys.exit(0)

# ── Resume 支援 ──
completed_dates: set[str] = set()
failed_sids: set[str] = set()
if args.resume and PROGRESS_FILE.exists():
    state = json.loads(PROGRESS_FILE.read_text())
    completed_dates = set(state.get("completed_dates", []))
    failed_sids = set(state.get("failed_sids", []))
    print(f"📌 續跑: {len(completed_dates)}/{len(snap_dates)} 週已完成, {len(failed_sids)} 檔失敗")

snap_dates = [d for d in snap_dates if d.isoformat() not in completed_dates]
if not snap_dates:
    print("✅ 所有週快照已完成")
    db.close()
    sys.exit(0)

# ── 主迴圈 ──
total_rows = 0
total_errors = 0

print(f"\n🚀 開始回補 {len(snap_dates)} 週, {len(valid_ids)} 檔")
print("=" * 60)

for week_idx, snapshot_date in enumerate(snap_dates, 1):
    sd_str = snapshot_date.isoformat()
    pending = [s for s in valid_ids if s not in failed_sids and s not in existing_by_date.get(sd_str, set())]
    if not pending:
        print(f"\n📅 [{week_idx}/{len(snap_dates)}] {sd_str}: 已完整，跳過")
        completed_dates.add(sd_str)
        continue

    print(f"\n📅 [{week_idx}/{len(snap_dates)}] {sd_str} — {len(pending)} 檔待補")

    batches = [pending[i:i + args.batch_size] for i in range(0, len(pending), args.batch_size)]
    week_rows = 0
    week_errors = 0

    for batch_idx, batch in enumerate(batches, 1):
        preview = ", ".join(batch[:5]) + ("..." if len(batch) > 5 else "")
        print(f"  📦 批次 {batch_idx}/{len(batches)} ({len(batch)} 檔): {preview}")

        for sid in batch:
            if client.is_banned():
                print(f"  🛑 {sid}: FinMind IP 被封，等待 60s...")
                time.sleep(60)
                continue

            try:
                rows = _fetch_for_stock(client, sid, sd_str)
                if rows:
                    with db.connection() as conn:
                        _upsert(conn, "institutional_holdings", rows, ["stock_id", "snapshot_date"])
                        conn.commit()
                    week_rows += len(rows)
                    print(f"  ✅ {sid}: {len(rows)} 筆")
                else:
                    print(f"  ⏭ {sid}: 無資料")
            except FinMindRateLimitError:
                print(f"  🛑 {sid}: 限流，儲存進度並等待 60s...")
                PROGRESS_FILE.write_text(json.dumps({
                    "completed_dates": sorted(completed_dates),
                    "failed_sids": sorted(failed_sids),
                    "current_week": sd_str,
                    "current_stock": sid,
                }, ensure_ascii=False, indent=2))
                time.sleep(60)
                # 重試一次
                try:
                    rows = _fetch_for_stock(client, sid, sd_str)
                    if rows:
                        with db.connection() as conn:
                            _upsert(conn, "institutional_holdings", rows, ["stock_id", "snapshot_date"])
                            conn.commit()
                        week_rows += len(rows)
                        print(f"  ✅ {sid} (重試): {len(rows)} 筆")
                except Exception as e2:
                    print(f"  ⚠️ {sid} 重試仍失敗: {e2}")
                    week_errors += 1
            except Exception as e:
                print(f"  ⚠️ {sid} 失敗: {e}")
                week_errors += 1
                failed_sids.add(sid)

        total_rows += week_rows
        total_errors += week_errors

        # 每批間隔
        if batch_idx < len(batches):
            print(f"  ⏳ 等待 {args.wait_seconds}s 避免限流...")
            time.sleep(args.wait_seconds)

    # 更新 existing_by_date 以避免重複請求（已成功的寫入）
    existing_by_date[sd_str] = existing_by_date.get(sd_str, set()) | set(pending)
    completed_dates.add(sd_str)

    # 每週結束儲存進度
    PROGRESS_FILE.write_text(json.dumps({
        "completed_dates": sorted(completed_dates),
        "failed_sids": sorted(failed_sids),
    }, ensure_ascii=False, indent=2))

# Cleanup
if PROGRESS_FILE.exists():
    PROGRESS_FILE.unlink()

print(f"\n{'='*60}")
print(f"✅ 回補完成: {total_rows} 行寫入")
print(f"   週快照: {len(completed_dates)}/{len(snap_dates) + len(completed_dates)} 完成")
if failed_sids:
    print(f"⚠️ {len(failed_sids)} 檔失敗: {', '.join(sorted(failed_sids)[:20])}")

db.close()