from __future__ import annotations
import re
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
import pandas as pd
import structlog
from sqlalchemy import text

from tw_quant_selector.data.database import Database, get_session
from tw_quant_selector.data.finmind_client import FinMindClient, FinMindRateLimitError
from tw_quant_selector.data.ingestion import (
    update_daily_prices,
    update_daily_prices_from_twse,
    update_valuations,
    update_valuations_from_twse,
    update_monthly_revenue,
    update_monthly_revenue_from_twse,
    update_financials,
    update_institutional_flows_from_twse,
    update_institutional_flows_from_tpex,
)
from tw_quant_selector.data.update_institutional_holdings import run_holdings_update
from scripts.seed_alert_rules import seed_alert_rules
from scripts.seed_default_strategy_config import seed_default_strategy_config


PIPELINE_STATE_FILE = Path(os.getenv("PIPELINE_STATE_FILE", "/tmp/pipeline_state.json"))


DATASETS_ALL = ["price", "per", "revenue", "financials", "institutional", "holdings"]


def save_pipeline_state(
    run_date: date,
    rate_limited_dataset: str,
    pending_stocks: list[str],
    failed_at: datetime,
    retry_after_minutes: int = 60,
) -> None:
    """Persist rate-limit state so a later run can resume."""
    state = {
        "run_date": run_date.isoformat(),
        "rate_limited_dataset": rate_limited_dataset,
        "pending_stocks": pending_stocks,
        "failed_at": failed_at.isoformat(),
        "retry_after_minutes": retry_after_minutes,
    }
    try:
        PIPELINE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PIPELINE_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
        log.info("pipeline.state_saved", file=str(PIPELINE_STATE_FILE), dataset=rate_limited_dataset)
    except Exception as e:
        log.error("pipeline.state_save_failed", error=str(e))


def load_pipeline_state() -> Optional[dict]:
    if not PIPELINE_STATE_FILE.exists():
        return None
    try:
        return json.loads(PIPELINE_STATE_FILE.read_text())
    except Exception as e:
        log.warning("pipeline.state_load_failed", error=str(e))
        return None


def clear_pipeline_state() -> None:
    if PIPELINE_STATE_FILE.exists():
        try:
            PIPELINE_STATE_FILE.unlink()
            log.info("pipeline.state_cleared")
        except Exception as e:
            log.warning("pipeline.state_clear_failed", error=str(e))

log = structlog.get_logger()

DATASETS = ["price", "per", "revenue", "financials", "institutional"]

_FINMIND_VALID_ID = re.compile(r"^\d{4}$|^00\d{3,4}$|^\d{5}[A-Z]?$|^\d{4}[A-Z]\d{0,2}$|^\d{6}[A-Z]?$")


def is_finmind_valid(stock_id: str) -> bool:
    return bool(_FINMIND_VALID_ID.match(stock_id))

DATASET_REQUESTS = {
    "price": 1,
    "per": 1,
    "revenue": 1,
    "financials": 1,
    "institutional": 0,
}

REQUESTS_PER_STOCK = sum(DATASET_REQUESTS.values())

STOCKS_PER_DAY = 120

FINANCIAL_START = "2022-01-01"
REVENUE_START = "2020-01-01"
PRICE_LOOKBACK_DAYS = 600


def _hash_bucket(stock_id: str, num_buckets: int) -> int:
    return (hash(stock_id) % num_buckets + num_buckets) % num_buckets


def _init_tracker(db: Database, as_of_date: date) -> dict[int, int]:
    """
    Initialize / validate ingestion_tracker bucket assignments.

    Bucket assignment is deterministic: stock_id → bucket = hash(stock_id) % HASH_MODULO.
    HASH_MODULO is recalculated only when stock count changes significantly, so
    bucket assignments stay stable across runs and each bucket ends up with ~STOCKS_PER_DAY stocks.

    Returns bucket_counts for get_todays_batch.
    """
    with db.connection() as conn:
        stocks_df = pd.DataFrame(conn.execute(text("SELECT stock_id FROM stocks")).fetchall(), columns=["stock_id"])

    if stocks_df.empty:
        return {}

    total_stocks = len(stocks_df)
    HASH_MODULO = max(1, total_stocks // STOCKS_PER_DAY)

    # Only rebuild if HASH_MODULO changed significantly (within ±2)
    HASH_MODULO_MIN = max(1, total_stocks // STOCKS_PER_DAY - 2)
    HASH_MODULO_MAX = max(1, total_stocks // STOCKS_PER_DAY + 2)

    with db.connection() as conn:
        rows = pd.DataFrame(conn.execute(text("SELECT stock_id, bucket FROM ingestion_tracker LIMIT 1")).fetchall(), columns=["stock_id", "bucket"])
        needs_rebuild = rows.empty

        if not needs_rebuild:
            existing_count = conn.execute("SELECT COUNT(DISTINCT stock_id) FROM ingestion_tracker").fetchone()
            if existing_count and existing_count[0] != total_stocks:
                needs_rebuild = True

    if not needs_rebuild:
        bucket_counts: dict[int, int] = {}
        with db.connection() as conn:
            rows = pd.DataFrame(conn.execute(text("SELECT bucket FROM ingestion_tracker")).fetchall(), columns=["bucket"])
            for _, r in rows.iterrows():
                bucket_counts[r["bucket"]] = bucket_counts.get(r["bucket"], 0) + 1
        log.info("scheduler.tracker.skip", total_stocks=total_stocks, buckets=len(bucket_counts),
                 stocks_per_bucket=round(total_stocks / max(1, len(bucket_counts)), 1))
        return bucket_counts

    log.info("scheduler.tracker.rebuild", total_stocks=total_stocks, hash_modulo=HASH_MODULO)

    with db.connection() as conn:
        conn.execute("DELETE FROM ingestion_tracker")
        conn.commit()

    bucket_counts: dict[int, int] = {}
    with db.connection() as conn:
        for _, row in stocks_df.iterrows():
            sid = row["stock_id"]
            b = _hash_bucket(sid, HASH_MODULO)
            bucket_counts[b] = bucket_counts.get(b, 0) + 1
            for ds in DATASETS:
                conn.execute(
                    "INSERT INTO ingestion_tracker VALUES (?, ?, ?, NULL, NULL, NULL)",
                    [sid, ds, b]
                )
        conn.commit()

    log.info("scheduler.tracker.init",
             total_stocks=total_stocks,
             hash_modulo=HASH_MODULO,
             buckets=len(bucket_counts),
             rows=total_stocks * len(DATASETS),
             stocks_per_bucket=round(total_stocks / max(1, len(bucket_counts)), 1),
             target=STOCKS_PER_DAY)
    return bucket_counts


def _get_requests_per_stock(sid: str, is_etf: bool) -> int:
    if is_etf:
        return 2
    return REQUESTS_PER_STOCK


def get_todays_batch(
    db: Database, run_date: Optional[date] = None, prioritize_holdings: bool = False
) -> list[dict]:
    """Return the list of stocks to process today.

    With ``prioritize_holdings=True``:
      - Holdings (portfolio.shares > 0) are placed FIRST in the batch.
      - If the holding count alone fills the day's quota, the bucket batch is
        skipped entirely so we don't waste tokens on non-holdings.
      - Otherwise, the bucket batch fills the remaining slots.
    """
    run_date = run_date or date.today()
    bucket_counts = _init_tracker(db, run_date)
    if not bucket_counts:
        return []

    total_buckets = len(bucket_counts)
    day_offset = run_date.toordinal() % total_buckets

    with db.connection() as conn:
        rows = pd.DataFrame(
            conn.execute(text("SELECT DISTINCT stock_id, bucket FROM ingestion_tracker WHERE bucket = :day_offset"),
                {"day_offset": day_offset}).fetchall(),
            columns=["stock_id", "bucket"])

    bucket_sids = [r["stock_id"] for r in rows.to_dict("records")]

    holding_sids: list[str] = []
    if prioritize_holdings:
        with db.connection() as conn:
            h_rows = conn.execute(
                "SELECT DISTINCT stock_id FROM portfolio WHERE shares > 0"
            ).fetchall()
        holding_sids = [r[0] for r in h_rows]
        log.info("scheduler.holdings_loaded", count=len(holding_sids))

    if prioritize_holdings and len(holding_sids) >= STOCKS_PER_DAY:
        # Holdings already fill the day — skip bucket entirely
        ordered = holding_sids[:STOCKS_PER_DAY]
        log.info("scheduler.batch.holdings_only",
                 holdings=len(ordered), bucket_skipped=True)
    else:
        # Merge: holdings first, then bucket (deduped)
        seen = set()
        ordered = []
        for sid in holding_sids + bucket_sids:
            if sid in seen:
                continue
            seen.add(sid)
            ordered.append(sid)
            if len(ordered) >= STOCKS_PER_DAY:
                break
        if prioritize_holdings and holding_sids:
            log.info("scheduler.batch.holdings_priority",
                     holdings=sum(1 for s in ordered if s in set(holding_sids)),
                     bucket_filled=len(ordered) - sum(1 for s in ordered if s in set(holding_sids)))

    # Hydrate is_etf for the final ordered list
    result: list[dict] = []
    with db.connection() as conn:
        for sid in ordered:
            etf_row = conn.execute(
                "SELECT is_etf FROM stocks WHERE stock_id = ?", [sid]
            ).fetchone()
            is_etf = bool(etf_row and etf_row[0]) if etf_row else False
            result.append({"stock_id": sid, "is_etf": is_etf})

    log.info("scheduler.todays_batch", date=run_date.isoformat(),
             bucket=day_offset, total_buckets=total_buckets,
             count=len(result), prioritize_holdings=prioritize_holdings,
             holdings=len(holding_sids))
    return result


def _update_tracker(db: Database, sid: str, ds: str, status: str, error: Optional[str] = None):
    with db.connection() as conn:
        conn.execute(
            "UPDATE ingestion_tracker SET last_updated = ?, last_status = ?, error_msg = ? WHERE stock_id = ? AND dataset = ?",
            [date.today(), status, error, sid, ds]
        )
        conn.commit()


def _filter_batch_by_tracker(
    db: Database, batch: list[dict], dataset: str
) -> list[str]:
    """Return only stocks in batch whose tracker says dataset is NOT 'ok'.

    Lets us skip stocks that were already successfully fetched in a previous run.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT stock_id, last_status FROM ingestion_tracker WHERE dataset = ?",
            [dataset]
        ).fetchall()
    ok_stocks = {r[0] for r in rows if r[1] == "ok"}
    pending = [s["stock_id"] for s in batch if s["stock_id"] not in ok_stocks]
    if len(pending) < len(batch):
        log.info("scheduler.tracker.skip_already_ok",
                 dataset=dataset, skipped=len(batch) - len(pending), pending=len(pending))
    return pending


def _log_to_db(db: Database, module: str, event: str, severity: str = "info"):
    with db.connection(read_only=False) as conn:
        conn.execute(
            text("INSERT INTO operation_logs (module, event, severity, created_at) VALUES (:module, :event, :severity, NOW())"),
            {"module": module, "event": event, "severity": severity}
        )
        conn.commit()


def _step_with_rate_limit_handling(
    db: Database,
    fn,
    dataset: str,
    run_date: date,
    remaining_stocks: list[str],
    results: dict[str, Any],
) -> bool:
    """Run a FinMind dataset function. On rate-limit, save state and return False."""
    try:
        n = fn()
        results["datasets"][dataset] = (results["datasets"].get(dataset, 0) or 0) + n
        
        # 成功後即時記錄
        _log_to_db(db, "ingestion", f"資料集 {dataset} 更新完成，共 {n} 筆", "info")
        
        return True
    except FinMindRateLimitError as e:
        # 限流中斷記錄
        _log_to_db(db, "ingestion", f"資料集 {dataset} 被限流中斷: {str(e)[:50]}", "warn")
        
        with db.connection() as conn:
            ok_rows = conn.execute(
                text("SELECT stock_id FROM ingestion_tracker WHERE dataset = :dataset AND stock_id = ANY(:sids) AND last_status = 'ok'"),
                {"dataset": dataset, "sids": remaining_stocks},
            ).fetchall()
        ok_set = {r[0] for r in ok_rows}
        not_attempted = [s for s in remaining_stocks if s not in ok_set]
        for sid in not_attempted:
            _update_tracker(db, sid, dataset, "rate_limited", str(e))

        msg = f"FinMind {dataset} 被限流，剩餘 {len(not_attempted)} 檔待辦"
        print(f"   🛑 {msg}")
        log.error("scheduler.rate_limited", dataset=dataset,
                  attempted=len(remaining_stocks) - len(not_attempted),
                  pending=len(not_attempted), error=str(e))
        save_pipeline_state(
            run_date=run_date,
            rate_limited_dataset=dataset,
            pending_stocks=not_attempted,
            failed_at=datetime.now(),
            retry_after_minutes=60,
        )
        results["rate_limited"] = dataset
        return False
    except Exception as e:
        print(f"   ❌ {dataset} 失敗: {e}")
        _log_to_db(db, "ingestion", f"資料集 {dataset} 失敗: {str(e)[:50]}", "error")
        log.error("scheduler.dataset_failed", dataset=dataset, error=str(e))
        results["datasets"][dataset] = 0
        return True


def _resolve_prioritize_holdings(db: Database, prioritize_holdings: Optional[bool]) -> bool:
    """Smart default: prioritize holdings iff the user has any.

    - prioritize_holdings=True   → force on
    - prioritize_holdings=False  → force off (user opted out)
    - prioritize_holdings=None   → auto: True if any row in portfolio.shares > 0
    """
    if prioritize_holdings is not None:
        return prioritize_holdings
    with db.connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM portfolio WHERE shares > 0"
        ).fetchone()
    has_holdings = bool(row and row[0] and row[0] > 0)
    log.info("scheduler.prioritize_holdings.auto",
             has_holdings=has_holdings, resolved=has_holdings)
    return has_holdings


def run_daily_update(
    db: Database,
    client: FinMindClient,
    run_date: Optional[date] = None,
    datasets: Optional[list[str]] = None,
    prioritize_holdings: Optional[bool] = None,
) -> dict[str, Any]:
    """Run daily data ingestion.

    Args:
        run_date: target date (default today)
        datasets: subset of DATASETS_ALL to run; default = all.
                 Examples: ["price"], ["revenue", "financials"], etc.
        prioritize_holdings: True/False force, or None for smart default
                             (auto-on if any portfolio.shares > 0 exists,
                              else fall back to bucket-only).

    Returns dict with keys: date, stocks_in_batch, datasets{...}, status, rate_limited
    """
    run_date = run_date or date.today()
    selected = set(datasets) if datasets else set(DATASETS_ALL)
    unknown = selected - set(DATASETS_ALL)
    if unknown:
        raise ValueError(f"Unknown datasets: {unknown}; valid: {DATASETS_ALL}")

    prioritize_holdings = _resolve_prioritize_holdings(db, prioritize_holdings)

    print(f"\n{'='*60}")
    print(f"📅 Scheduler 執行日期: {run_date.isoformat()}")
    print(f"   選取 datasets: {', '.join(sorted(selected)) or '(全部)'}")
    if prioritize_holdings:
        print(f"   ⭐ 持股優先模式: holdings 會先抓")
    else:
        print(f"   📊 一般模式: hash bucket 隨機抽 120 檔")
    print(f"{'='*60}")

    batch = get_todays_batch(db, run_date, prioritize_holdings=prioritize_holdings)
    if not batch:
        print("⚠️ 今日沒有股票需要處理（batch 為空）")
        return {"status": "skipped", "reason": "no stocks in batch", "stocks": 0}

    stock_ids = [s["stock_id"] for s in batch]
    etf_ids = [s["stock_id"] for s in batch if s["is_etf"]]
    stock_ids_only = [s["stock_id"] for s in batch if not s["is_etf"]]
    finmind_valid = [s["stock_id"] for s in batch if is_finmind_valid(s["stock_id"])]

    print(f"\n📦 今日批次資訊:")
    print(f"   總股票數: {len(batch)}")
    print(f"   ETF 數量: {len(etf_ids)}")
    print(f"   個股數量: {len(stock_ids_only)}")
    print(f"   FinMind 有效: {len(finmind_valid)} (格式符合)")
    print(f"   無效 ID (跳過): {len(stock_ids) - len(finmind_valid)}")

    results: dict[str, Any] = {
        "date": run_date.isoformat(),
        "stocks_in_batch": len(batch),
        "finmind_valid": len(finmind_valid),
        "invalid_skipped": len(stock_ids) - len(finmind_valid),
        "datasets": {},
        "selected": sorted(selected),
    }

    price_start = run_date - timedelta(days=PRICE_LOOKBACK_DAYS)
    day_iso = run_date.isoformat()

    print(f"\n{'─'*60}")
    print(f"🔄 開始下載資料集...")
    print(f"{'─'*60}")

    # ── Dataset: price (TWSE primary + FinMind fallback) ──
    if "price" in selected:
        twse_date = None
        try:
            print(f"   [1a] 從 TWSE 取得所有股票最新股價...")
            n_twse, twse_date = update_daily_prices_from_twse(db)
            results["datasets"]["price"] = (results["datasets"].get("price", 0) or 0) + n_twse
            print(f"   ✅ TWSE 股價完成: {n_twse} 筆記錄 (日期: {twse_date})")
            for sid in stock_ids:
                _update_tracker(db, sid, "price", "ok")
        except Exception as e:
            print(f"   ⚠️ TWSE 股價失敗，將以 FinMind 處理: {e}")
            log.warning("scheduler.twse_price_failed", error=str(e))

        finmind_needed = []
        if twse_date:
            with db.connection() as conn:
                for sid in finmind_valid:
                    row = conn.execute(
                        "SELECT 1 FROM daily_prices WHERE stock_id = ? AND trade_date = ?",
                        [sid, twse_date]
                    ).fetchone()
                    if not row:
                        finmind_needed.append(sid)
        else:
            finmind_needed = finmind_valid

        # Filter to only those not already up-to-date
        finmind_needed = _filter_batch_by_tracker(db, [{"stock_id": s} for s in finmind_needed], "price")

        if finmind_needed:
            print(f"   [1b] 從 FinMind 補下載 {len(finmind_needed)} 檔股票股價...")
            ok = _step_with_rate_limit_handling(
                db,
                lambda: update_daily_prices(db, client, finmind_needed, price_start, run_date),
                "price", run_date, finmind_needed, results,
            )
            if not ok:
                _print_summary(results, batch, finmind_valid, etf_ids, stock_ids_only)
                return results
            print(f"   ✅ FinMind 股價完成")
        else:
            print(f"   ℹ️ TWSE + tracker 已涵蓋，無需 FinMind 補下載")

    # ── Dataset: per (TWSE + FinMind fallback) ──
    if "per" in selected:
        try:
            print(f"   [2] 下載本益比/淨值比/殖利率 (TWSE BWIBBU_ALL)...")
            n_twse, n_mc = update_valuations_from_twse(db)
            results["datasets"]["per"] = (results["datasets"].get("per", 0) or 0) + n_twse
            print(f"   ✅ TWSE 估值完成: {n_twse} 筆記錄, 市值計算 {n_mc} 檔")
            for sid in stock_ids:
                _update_tracker(db, sid, "per", "ok")
        except Exception as e:
            print(f"   ❌ TWSE 估值失敗: {e}")
            log.error("scheduler.twse_valuations_failed", error=str(e))
            results["datasets"]["per"] = 0

        # FinMind per-stock historical PE
        pending_per = _filter_batch_by_tracker(db, batch, "per")
        if pending_per:
            print(f"   [3] 補本益比 (FinMind) for {len(pending_per)} 檔...")
            ok = _step_with_rate_limit_handling(
                db,
                lambda: update_valuations(db, client, pending_per, "2022-01-01", day_iso),
                "per", run_date, pending_per, results,
            )
            if not ok:
                _print_summary(results, batch, finmind_valid, etf_ids, stock_ids_only)
                return results
            print(f"   ✅ FinMind 本益比完成")

    # ── Dataset: revenue (TWSE current month + FinMind historical) ──
    if "revenue" in selected:
        try:
            print(f"   [4] 下載月營收 (TWSE t187ap05_L)...")
            n_twse_rev = update_monthly_revenue_from_twse(db)
            results["datasets"]["revenue"] = (results["datasets"].get("revenue", 0) or 0) + n_twse_rev
            print(f"   ✅ TWSE 月營收完成: {n_twse_rev} 筆記錄")
            for sid in stock_ids:
                _update_tracker(db, sid, "revenue", "ok")
        except Exception as e:
            print(f"   ❌ TWSE 月營收失敗: {e}")
            log.error("scheduler.twse_revenue_failed", error=str(e))
            results["datasets"]["revenue"] = 0

        pending_rev = _filter_batch_by_tracker(db, batch, "revenue")
        if pending_rev:
            print(f"   [補] 下載月營收 (FinMind) for {len(pending_rev)} 檔股票...")
            ok = _step_with_rate_limit_handling(
                db,
                lambda: update_monthly_revenue(db, client, pending_rev, REVENUE_START, day_iso),
                "revenue", run_date, pending_rev, results,
            )
            if not ok:
                _print_summary(results, batch, finmind_valid, etf_ids, stock_ids_only)
                return results
            print(f"   ✅ FinMind 月營收完成")

    # ── Dataset: financials ──
    if "financials" in selected:
        pending_fin = _filter_batch_by_tracker(db, batch, "financials")
        if pending_fin:
            print(f"   [6] 下載財報 (financials) for {len(pending_fin)} 檔...")
            ok = _step_with_rate_limit_handling(
                db,
                lambda: update_financials(db, client, pending_fin, FINANCIAL_START, day_iso),
                "financials", run_date, pending_fin, results,
            )
            if not ok:
                _print_summary(results, batch, finmind_valid, etf_ids, stock_ids_only)
                return results
            print(f"   ✅ 財報完成")

    # ── Dataset: institutional (TWSE + TPEX) ──
    if "institutional" in selected:
        # TWSE T86 與 TPEX OpenAPI 的當日資料通常要到收盤後 ~18:30 才會發布。
        # 改用「daily_prices 表裡最新的 trade_date」當目標日，
        # 避免在同日抓取（會拿到 "沒有符合條件的資料"）造成 false 0 row。
        with db.connection() as conn:
            latest_price_row = conn.execute(
                "SELECT MAX(trade_date) FROM daily_prices"
            ).fetchone()
        inst_target_date = (
            str(latest_price_row[0]) if latest_price_row and latest_price_row[0] else day_iso
        )
        if inst_target_date != day_iso:
            print(f"   ℹ️ T86 目標日改用最新 trade_date={inst_target_date}（today={day_iso} 通常尚未發布）")

        try:
            print(f"   [7a] 下載三大法人買賣超 (TWSE T86) for {inst_target_date}...")
            n = update_institutional_flows_from_twse(db, inst_target_date)
            results["datasets"]["institutional"] = (results["datasets"].get("institutional", 0) or 0) + n
            print(f"   ✅ TWSE 法人買賣超完成: {n} 筆記錄")
        except Exception as e:
            print(f"   ❌ TWSE 法人買賣超失敗: {e}")
            log.error("scheduler.dataset_failed", dataset="institutional.twse", error=str(e))

        try:
            print(f"   [7b] 下載三大法人買賣超 (TPEX) for {inst_target_date}...")
            n = update_institutional_flows_from_tpex(db, inst_target_date)
            results["datasets"]["institutional"] = (results["datasets"].get("institutional", 0) or 0) + n
            print(f"   ✅ TPEX 法人買賣超完成: {n} 筆記錄")
        except Exception as e:
            print(f"   ❌ TPEX 法人買賣超失敗: {e}")
            log.error("scheduler.dataset_failed", dataset="institutional.tpex", error=str(e))

        for sid in stock_ids:
            _update_tracker(db, sid, "institutional", "ok")

    # ── Weekly: institutional holdings (Monday only) ──
    if "holdings" in selected and run_date.weekday() == 0:
        print(f"   [週] 下載法人持股比率 (FinMind)...")
        ok = _step_with_rate_limit_handling(
            db,
            lambda: run_holdings_update(db, client, snapshot_date=run_date),
            "holdings", run_date, stock_ids_only, results,
        )
        if ok:
            print(f"   ✅ 法人持股比率完成")
    elif "holdings" in selected:
        print(f"   ℹ️ 跳過法人持股比率（僅週一執行）")

    db.checkpoint()

    # Successful completion → clear any pending rate-limit state
    if "rate_limited" not in results:
        clear_pipeline_state()

    # ── Run one-time seeds if needed + weekly guru scores ──
    _ensure_seeds(db, run_date)

    _print_summary(results, batch, finmind_valid, etf_ids, stock_ids_only)

    log.info("scheduler.daily_complete",
             date=run_date.isoformat(),
             stocks=len(stock_ids_only),
             etfs=len(etf_ids),
             valid=len(finmind_valid),
             skipped=len(stock_ids) - len(finmind_valid),
             selected=sorted(selected),
             datasets={k: v for k, v in results["datasets"].items() if v},
             rate_limited=results.get("rate_limited"))

    return results


def _ensure_seeds(db: Database, run_date: date):
    """Run one-time seeds and weekly guru scores after pipeline completes."""
    session = get_session()
    try:
        # ── One-time: alert rules if empty ──
        has_rules = session.execute(
            "SELECT 1 FROM alert_rules LIMIT 1"
        ).fetchone()
        if not has_rules:
            print("   🌱 首次執行：初始化 alert 規則...")
            seed_alert_rules()

        # ── One-time: strategy config if empty ──
        has_config = session.execute(
            "SELECT 1 FROM strategy_config_history LIMIT 1"
        ).fetchone()
        if not has_config:
            print("   🌱 首次執行：初始化策略設定...")
            seed_default_strategy_config()

        # ── Weekly (Saturday): guru_scores ──
        if run_date.weekday() == 5:  # Saturday
            last_friday = run_date - timedelta(days=1)
            has_guru = session.execute(
                "SELECT 1 FROM guru_scores WHERE score_date = :sd AND guru = 'piotroski' LIMIT 1",
                {"sd": last_friday},
            ).fetchone()
            if not has_guru:
                print(f"   📊 每週計算：Piotroski F-Score（as_of={last_friday}）...")
                _compute_guru_scores_from_db(session, last_friday)
                session.commit()
                print(f"   ✅ Guru scores 完成")
    finally:
        session.close()


def _compute_guru_scores_from_db(session, as_of_date: date):
    """Compute Piotroski F-Score using financials table only (no FinMind API).
    
    Uses the last 8 quarters of data: latest 4 = 'this year', prior 4 = 'last year'.
    """
    import json
    from collections import defaultdict

    # Get all stocks with financial data
    stocks = session.execute(
        "SELECT DISTINCT stock_id FROM financials WHERE roa IS NOT NULL"
    ).fetchall()

    for (sid,) in stocks:
        rows = session.execute("""
            SELECT year_quarter, revenue, net_income, roa, debt_to_equity,
                   gross_margin, total_assets, operating_cash_flow, current_ratio
            FROM financials WHERE stock_id = :sid AND roa IS NOT NULL
            ORDER BY year_quarter DESC LIMIT 8
        """, {"sid": sid}).fetchall()

        if len(rows) < 4:
            continue

        # Split: first 4 = this year, next (up to 4) = last year
        this = rows[:4]
        last = rows[4:8] if len(rows) >= 8 else None

        def _agg(qs):
            rev = sum(float(r.revenue) for r in qs if r.revenue)
            ni = sum(float(r.net_income) for r in qs if r.net_income)
            cfo = sum(float(r.operating_cash_flow) for r in qs if r.operating_cash_flow)
            cr_vals = [float(r.current_ratio) for r in qs if r.current_ratio]
            ta_vals = [float(r.total_assets) for r in qs if r.total_assets]
            roa_vals = [float(r.roa) for r in qs if r.roa]
            dte_vals = [float(r.debt_to_equity) for r in qs if r.debt_to_equity]
            gm_vals = [float(r.gross_margin) for r in qs if r.gross_margin]
            avg_roa = sum(roa_vals) / len(roa_vals) if roa_vals else None
            avg_dte = sum(dte_vals) / len(dte_vals) if dte_vals else None
            avg_gm = sum(gm_vals) / len(gm_vals) if gm_vals else None
            avg_cr = sum(cr_vals) / len(cr_vals) if cr_vals else None
            avg_ta = sum(ta_vals) / len(ta_vals) if ta_vals else None
            # Asset turnover = revenue / total_assets (real ta if available)
            at = None
            if avg_ta and avg_ta != 0:
                at = rev / avg_ta
            elif ni and avg_roa and avg_roa != 0:
                ta = ni / avg_roa
                at = rev / ta if ta else None
            return {
                "revenue": rev, "net_income": ni, "operating_cash_flow": cfo,
                "roa": avg_roa, "debt_to_equity": avg_dte,
                "gross_margin": avg_gm, "asset_turnover": at,
                "current_ratio": avg_cr,
            }

        t = _agg(this)
        l = _agg(last) if last else {}

        def v(key): return t.get(key)
        def p(key): return l.get(key) if l else None

        def to_float(x):
            if x is None: return None
            try: return float(x)
            except: return None

        roa_val = to_float(v("roa"))
        roa_last = to_float(p("roa"))
        ni_val = to_float(v("net_income"))
        cfo_val = to_float(v("operating_cash_flow"))
        dte_val = to_float(v("debt_to_equity"))
        dte_last = to_float(p("debt_to_equity"))
        gm_val = to_float(v("gross_margin"))
        gm_last = to_float(p("gross_margin"))
        at_val = to_float(v("asset_turnover"))
        at_last = to_float(p("asset_turnover"))
        cr_val = to_float(v("current_ratio"))
        cr_last = to_float(p("current_ratio"))

        # Use real CFO if available, fallback to net_income
        cf_for_accrual = cfo_val if cfo_val is not None else ni_val
        accrual_val = (ni_val - cf_for_accrual) if (ni_val is not None and cf_for_accrual is not None) else None

        criteria = {
            # Profitability
            "roa_positive": roa_val is not None and roa_val > 0,
            "roa_value": roa_val,
            "roa_threshold": 0,
            "cf_positive": cfo_val is not None and cfo_val > 0,
            "cf_label": "Operating Cash Flow" if cfo_val is not None else "Net Income（替代）",
            "cf_value": cfo_val if cfo_val is not None else ni_val,
            "cf_threshold": 0,
            "delta_roa_positive": roa_val is not None and roa_last is not None and roa_val > roa_last,
            "delta_roa_value": (roa_val - roa_last) if (roa_val is not None and roa_last is not None) else None,
            "delta_roa_last": roa_last,
            "delta_roa_threshold": 0,
            "accruals_negative": accrual_val is not None and accrual_val < 0,
            "accruals_label": "Accruals (NI - CFO)" if cfo_val is not None else "Net Income > 0（替代）",
            "accruals_value": accrual_val,
            "accruals_threshold": 0,
            # Funding
            "delta_leverage_negative": dte_val is not None and dte_last is not None and dte_val < dte_last,
            "delta_leverage_value": dte_val,
            "delta_leverage_last": dte_last,
            "delta_leverage_threshold": "current < prior",
            "delta_current_ratio_positive": cr_val is not None and cr_last is not None and cr_val > cr_last,
            "delta_current_ratio_label": "Current Ratio" if cr_val is not None else "（尚無資料）",
            "delta_current_ratio_value": cr_val,
            "delta_current_ratio_last": cr_last,
            "no_new_shares": True,
            "no_new_shares_label": "（尚無資料，預設通過）",
            # Operating Efficiency
            "delta_gross_margin_positive": gm_val is not None and gm_last is not None and gm_val > gm_last,
            "delta_gross_margin_value": gm_val,
            "delta_gross_margin_last": gm_last,
            "delta_gross_margin_threshold": 0,
            "delta_asset_turnover_positive": at_val is not None and at_last is not None and at_val > at_last,
            "delta_asset_turnover_value": at_val,
            "delta_asset_turnover_last": at_last,
            "delta_asset_turnover_threshold": 0,
        }

        score = sum(1 for k in [
            "roa_positive", "cf_positive", "delta_roa_positive", "accruals_negative",
            "delta_leverage_negative", "delta_current_ratio_positive", "no_new_shares",
            "delta_gross_margin_positive", "delta_asset_turnover_positive",
        ] if criteria.get(k))

        session.execute("""
            INSERT INTO guru_scores (score_date, stock_id, guru, score, pass_filter, criteria_detail)
            VALUES (:sd, :sid, 'piotroski', :score, :pass, :det)
            ON CONFLICT (score_date, stock_id, guru)
            DO UPDATE SET score = EXCLUDED.score, pass_filter = EXCLUDED.pass_filter,
                          criteria_detail = EXCLUDED.criteria_detail
        """, {
            "sd": as_of_date, "sid": sid, "score": score,
            "pass": score >= 7, "det": json.dumps({k: v for k, v in criteria.items() if v is not None}),
        })


def _print_summary(
    results: dict, batch: list[dict], finmind_valid: list[str],
    etf_ids: list[str], stock_ids_only: list[str],
) -> None:
    """Print final summary table."""
    print(f"\n{'='*60}")
    print(f"📊 執行完成:")
    print(f"   日期: {results['date']}")
    print(f"   選取 datasets: {', '.join(results.get('selected', []))}")
    if "rate_limited" in results:
        print(f"   🛑 限流中斷: {results['rate_limited']} (剩餘標的已存於 pipeline state)")
    print(f"   資料集:")
    for ds, n in results["datasets"].items():
        if n:
            print(f"     - {ds}: {n} 筆記錄")
    print(f"{'='*60}\n")