"""
一次性財務回補：為 DB 中缺少 financials 資料的股票補全從 FinMind 拉取的歷史季財報。

用法：
  docker compose exec app python3 scripts/backfill_financials.py

可選參數：
  --batch-size 10        每批幾檔（FinMind 限流控制，建議 5~10）
  --wait-seconds 60      批次間等待秒數（建議 60~180）
  --stock-ids "2330,2317" 只補指定股票（不留則全部）
  --resume               從上次中斷處續跑（讀取 .backfill_progress_fin.json）
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tw_quant_selector.data.database import Database
from tw_quant_selector.data.finmind_client import FinMindClient, FinMindRateLimitError
from tw_quant_selector.data.ingestion import _upsert, _pivot_financials, _pivot_balance_sheet, _pivot_cash_flows

PROGRESS_FILE = Path(__file__).resolve().parent.parent / ".backfill_progress_fin.json"

parser = argparse.ArgumentParser(description="一次性財務回補")
parser.add_argument("--batch-size", type=int, default=10, help="每批處理檔數")
parser.add_argument("--wait-seconds", type=int, default=60, help="批次間等待秒數")
parser.add_argument("--stock-ids", type=str, help="逗號分隔的股票代號（不留則全部）")
parser.add_argument("--resume", action="store_true", help="從上次中斷處續跑")
parser.add_argument("token", nargs="?", help="FinMind API token（或用 FINMIND_TOKEN env）")
args = parser.parse_args()

token = args.token or os.environ.get("FINMIND_TOKEN", "")
if not token:
    parser.print_help()
    sys.exit(1)

db = Database()
client = FinMindClient(token)

start = "2019-01-01"
end = date.today().isoformat()

print(f"📅 回補範圍: {start} ~ {end}")
print(f"📦 每批: {args.batch_size} 檔, 間隔: {args.wait_seconds}s")

# ── Determine stock list ──
if args.stock_ids:
    all_ids = [s.strip() for s in args.stock_ids.split(",") if s.strip()]
    print(f"🎯 指定標的: {len(all_ids)} 檔")
else:
    with db.connection() as conn:
        rows = conn.execute("""
            SELECT stock_id FROM stocks
            WHERE is_etf = false
            ORDER BY stock_id
        """).fetchall()
    all_ids = [r[0] for r in rows]
    print(f"📋 全部上市櫃: {len(all_ids)} 檔")

# ── Check which already have financials ──
with db.connection() as conn:
    rows = conn.execute("""
        SELECT DISTINCT stock_id FROM financials
    """).fetchall()
existing = {r[0] for r in rows}
print(f"📊 已有 financials: {len(existing)} 檔, 缺 {len(all_ids) - len(existing)} 檔")

# Only backfill those missing data
all_ids = [s for s in all_ids if s not in existing]
if not all_ids:
    print("✅ 沒有需要補的財務資料")
    db.close()
    sys.exit(0)

# ── Resume support ──
completed: set[str] = set()
failed_sids: set[str] = set()
if args.resume and PROGRESS_FILE.exists():
    state = json.loads(PROGRESS_FILE.read_text())
    completed = set(state.get("completed", []))
    failed_sids = set(state.get("failed", []))
    print(f"📌 續跑: {len(completed)} 已完成, {len(failed_sids)} 失敗 (跳過)")
    all_ids = [s for s in all_ids if s not in completed and s not in failed_sids]

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

# ── Batches ──
batches = [valid_ids[i:i + args.batch_size] for i in range(0, len(valid_ids), args.batch_size)]
total_batches = len(batches)

print(f"\n🚀 開始回補 ({len(valid_ids)} 檔, {total_batches} 批)")
print("=" * 60)

total_rows = 0

for batch_idx, batch in enumerate(batches, 1):
    preview = ", ".join(batch[:5]) + ("..." if len(batch) > 5 else "")
    print(f"\n📥 批次 {batch_idx}/{total_batches} ({len(batch)} 檔): {preview}")

    n = 0
    for sid in batch:
        try:
            # Fetch all three datasets
            fin_raw = client.get_financials(sid, start, end)
            bs_raw = client.get_balance_sheet(sid, start, end)
            cf_raw = client.get_cash_flows(sid, start, end)

            # Pivot
            fin_df = _pivot_financials(fin_raw)
            bs_df = _pivot_balance_sheet(bs_raw)
            cf_df = _pivot_cash_flows(cf_raw)

            if fin_df.empty:
                print(f"   ⏭ {sid}: 無財務資料 (skip)")
                completed.add(sid)
                continue

            # Merge balance sheet columns
            if not bs_df.empty:
                bs_cols = ["stock_id", "date", "equity", "liabilities", "total_assets",
                           "current_assets", "current_liabilities", "cash"]
                avail = [c for c in bs_cols if c in bs_df.columns]
                merged = fin_df.merge(bs_df[avail], on=["stock_id", "date"], how="left")
            else:
                merged = fin_df.copy()
                for c in ["equity", "liabilities", "total_assets",
                          "current_assets", "current_liabilities", "cash"]:
                    merged[c] = None

            # Merge cash flow columns
            if not cf_df.empty:
                cf_avail = [c for c in ["stock_id", "date", "operating_cash_flow"] if c in cf_df.columns]
                merged = merged.merge(cf_df[cf_avail], on=["stock_id", "date"], how="left")
            else:
                merged["operating_cash_flow"] = None

            # Compute derived fields (same logic as update_financials)
            for num, den, col in [
                ("net_income", "equity", "roe"),
                ("net_income", "total_assets", "roa"),
                ("liabilities", "equity", "debt_to_equity"),
            ]:
                if den in merged.columns and merged[den].notna().any():
                    merged[col] = merged[num] / merged[den].replace(0, pd.NA)
                else:
                    merged[col] = pd.NA

            # current_ratio
            if "current_assets" in merged.columns and "current_liabilities" in merged.columns:
                safe_cl = merged["current_liabilities"].replace(0, pd.NA)
                merged["current_ratio"] = merged["current_assets"] / safe_cl
            else:
                merged["current_ratio"] = pd.NA

            # Fallback: operating_cash_flow → net_income if missing
            if "operating_cash_flow" in merged.columns:
                merged["operating_cash_flow"] = merged["operating_cash_flow"].fillna(merged["net_income"])

            out_cols = ["stock_id", "year_quarter", "revenue", "net_income", "eps",
                        "announcement_date"]
            for c in ["gross_profit", "operating_income", "roe", "roa",
                       "gross_margin", "operating_margin", "debt_to_equity",
                       "total_assets", "current_ratio", "operating_cash_flow"]:
                if c in merged.columns:
                    out_cols.append(c)
            result = merged[out_cols].to_dict("records")

            with db.connection() as conn:
                _upsert(conn, "financials", result, ["stock_id", "year_quarter"])
                conn.commit()
            n += len(result)
            completed.add(sid)
            print(f"   ✅ {sid}: {len(result)} 季財報")

        except FinMindRateLimitError as e:
            print(f"   🛑 {sid} 限流: {e}")
            # Save progress and exit
            PROGRESS_FILE.write_text(json.dumps({
                "completed": sorted(completed), "failed": sorted(failed_sids),
                "last_batch": batch_idx, "total_batches": total_batches,
            }, ensure_ascii=False, indent=2))
            print(f"   📌 進度檔: {PROGRESS_FILE}（--resume 續跑）")
            wait_min = 60
            print(f"   ⏳ 等待 {wait_min} 分鐘後重試...")
            time.sleep(wait_min * 60)
            # Retry same sid after wait
            try:
                fin_raw = client.get_financials(sid, start, end)
                bs_raw = client.get_balance_sheet(sid, start, end)
                cf_raw = client.get_cash_flows(sid, start, end)
                fin_df = _pivot_financials(fin_raw)
                if not fin_df.empty:
                    # full pivot + merge + upsert (same as above)
                    bs_df = _pivot_balance_sheet(bs_raw)
                    cf_df = _pivot_cash_flows(cf_raw)
                    if not bs_df.empty:
                        bs_cols = ["stock_id", "date", "equity", "liabilities", "total_assets",
                                   "current_assets", "current_liabilities", "cash"]
                        avail = [c for c in bs_cols if c in bs_df.columns]
                        merged = fin_df.merge(bs_df[avail], on=["stock_id", "date"], how="left")
                    else:
                        merged = fin_df.copy()
                        for c in ["equity", "liabilities", "total_assets",
                                  "current_assets", "current_liabilities", "cash"]:
                            merged[c] = None
                    if not cf_df.empty:
                        cf_avail = [c for c in ["stock_id", "date", "operating_cash_flow"] if c in cf_df.columns]
                        merged = merged.merge(cf_df[cf_avail], on=["stock_id", "date"], how="left")
                    else:
                        merged["operating_cash_flow"] = None
                    for num, den, col in [
                        ("net_income", "equity", "roe"),
                        ("net_income", "total_assets", "roa"),
                        ("liabilities", "equity", "debt_to_equity"),
                    ]:
                        if den in merged.columns and merged[den].notna().any():
                            merged[col] = merged[num] / merged[den].replace(0, pd.NA)
                        else:
                            merged[col] = pd.NA
                    if "current_assets" in merged.columns and "current_liabilities" in merged.columns:
                        safe_cl = merged["current_liabilities"].replace(0, pd.NA)
                        merged["current_ratio"] = merged["current_assets"] / safe_cl
                    else:
                        merged["current_ratio"] = pd.NA
                    if "operating_cash_flow" in merged.columns:
                        merged["operating_cash_flow"] = merged["operating_cash_flow"].fillna(merged["net_income"])
                    out_cols = ["stock_id", "year_quarter", "revenue", "net_income", "eps",
                                "announcement_date"]
                    for c in ["gross_profit", "operating_income", "roe", "roa",
                               "gross_margin", "operating_margin", "debt_to_equity",
                               "total_assets", "current_ratio", "operating_cash_flow"]:
                        if c in merged.columns:
                            out_cols.append(c)
                    result = merged[out_cols].to_dict("records")
                    with db.connection() as conn:
                        _upsert(conn, "financials", result, ["stock_id", "year_quarter"])
                        conn.commit()
                    n += len(result)
                    completed.add(sid)
                    print(f"   ✅ {sid}: {len(result)} 季財報 (重試成功)")
                else:
                    completed.add(sid)
                    print(f"   ⏭ {sid}: 無財務資料 (重試後)")
            except Exception as e2:
                print(f"   ⚠️ {sid} 重試仍失敗: {e2}")
                failed_sids.add(sid)

        except Exception as e:
            print(f"   ⚠️ {sid} 失敗: {e}")
            failed_sids.add(sid)

    if n > 0:
        print(f"   📊 本批小計: {n} 行")
        total_rows += n

    # Save progress after each batch
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
print(f"✅ 回補完成: {total_rows} 行寫入 {len(completed)} 檔")
if failed_sids:
    print(f"⚠️ {len(failed_sids)} 檔失敗: {', '.join(sorted(failed_sids)[:10])}...")

db.close()
