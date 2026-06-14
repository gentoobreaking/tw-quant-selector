from __future__ import annotations
from __future__ import annotations

import gc
import hashlib
import time
from datetime import date, datetime, timedelta
from typing import Any
import numpy as np
import pandas as pd
import structlog

from tw_quant_selector.data.database import Database, validate_table_name
from tw_quant_selector.data.finmind_client import FinMindClient, FinMindRateLimitError
from tw_quant_selector.data.twstock_client import (
    fetch_twse_daily_prices_all,
    fetch_twse_valuations_all,
    fetch_twse_revenue_all,
    fetch_twse_institutional_all,
    fetch_tpex_institutional_all,
    fetch_with_retry,
    is_trading_day,
)

log = structlog.get_logger()

# ── TTL Cache (30 minutes for TWSE/TPEX batch APIs) ──

_TTL_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 1800  # 30 minutes


def _cache_key(fn_name: str, *args, **kwargs) -> str:
    raw = f"{fn_name}:{args}:{kwargs}"
    return hashlib.md5(raw.encode()).hexdigest()


def _fetch_with_cache(fetch_fn, fn_name: str, *args, **kwargs) -> Any:
    """Call fetch_fn with a TTL cache. Cache key = hash(function name + args).
    
    Only caches non-None, non-empty results to avoid serving stale empties.
    """
    key = _cache_key(fn_name, *args, **kwargs)
    now = time.monotonic()
    cached = _TTL_CACHE.get(key)
    if cached and (now - cached[0]) < _TTL_SECONDS:
        log.info("cache.hit", fn=fn_name)
        return cached[1]
    result = fetch_fn(*args, **kwargs)
    if result is not None and result != [] and result != {}:
        _TTL_CACHE[key] = (now, result)
        log.info("cache.miss", fn=fn_name)
    else:
        log.info("cache.skip_empty", fn=fn_name)
    return result


def _ttl_cache_info() -> dict[str, Any]:
    now = time.monotonic()
    entries = [(k, now - v[0]) for k, v in _TTL_CACHE.items()]
    return {
        "size": len(entries),
        "entries": [{"key": k[:16], "age_seconds": int(a)} for k, a in entries],
    }


def _ttl_cache_clear() -> None:
    _TTL_CACHE.clear()


def update_tracker_for_stock(
    conn, sid: str, dataset: str, status: str, error: Optional[str] = None
) -> None:
    """Per-stock tracker write. Call within an open transaction; does NOT commit.

    Use this in per-stock loops so the tracker reflects progress in real time.
    Status values:
      - "ok"            : data fetched & committed
      - "rate_limited"  : FinMind 402 hit on this stock; caller will re-raise
      - "failed"        : generic error for this stock; caller continues
    """
    conn.execute(
        "UPDATE ingestion_tracker SET last_updated = ?, last_status = ?, error_msg = ? "
        "WHERE stock_id = ? AND dataset = ?",
        [date.today(), status, error, sid, dataset],
    )


FINANCIAL_TYPE_MAP = {
    "Revenue": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncome": "operating_income",
    "IncomeAfterTaxes": "net_income",
    "IncomeAfterTax": "net_income",
    "EPS": "eps",
    "CostOfGoodsSold": "cost_of_goods_sold",
    "OperatingExpenses": "operating_expenses",
}

BALANCE_SHEET_TYPE_MAP = {
    "EquityAttributableToOwnersOfParent": "equity",
    "Equity": "equity",
    "Liabilities": "liabilities",
    "TotalAssets": "total_assets",
    "CurrentAssets": "current_assets",
    "CurrentLiabilities": "current_liabilities",
}

CASH_FLOW_TYPE_MAP = {
    "OperatingCashFlow": "operating_cash_flow",
}

PRICE_COLUMNS = {
    "stock_id": "stock_id", "date": "trade_date", "open": "open",
    "max": "high", "min": "low", "close": "close",
    "Trading_Volume": "volume", "Trading_money": "amount",
}

VALUATION_COLUMNS = {
    "stock_id": "stock_id", "date": "trade_date",
    "PER": "pe_ratio", "PBR": "pb_ratio", "dividend_yield": "dividend_yield",
}

REVENUE_COLUMNS = {
    "stock_id": "stock_id", "revenue_month": "year_month",
    "revenue": "revenue", "date": "announcement_date",
}


def _clean_nan(v):
    if isinstance(v, float) and np.isnan(v):
        return None
    return v

def _upsert(conn, table: str, rows: list[dict], pk_cols: list[str]):
    if not rows:
        return 0
    validate_table_name(table)
    cols = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    pk_condition = " AND ".join(f"{c} = ?" for c in pk_cols)
    count = 0
    for row in rows:
        vals = [_clean_nan(row.get(c)) for c in cols]
        pk_vals = [row.get(c) for c in pk_cols]
        conn.execute(f"DELETE FROM {table} WHERE {pk_condition}", pk_vals)
        conn.execute(f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", vals)
        count += 1
    conn.commit()
    return count


def _date_to_year_quarter(d: str) -> str:
    dt = pd.Timestamp(d)
    return f"{dt.year}Q{(dt.month - 1) // 3 + 1}"


def _get_max_date(db: Database, table: str, stock_id: str, col: str) -> Optional[str]:
    """Get latest date value for a (stock_id, table). Returns ISO string or None.

    Returns None if no data exists for this stock in the table.
    """
    validate_table_name(table)
    with db.connection() as conn:
        row = conn.execute(
            f"SELECT MAX({col}) FROM {table} WHERE stock_id = ?",
            [stock_id]
        ).fetchone()
    if row and row[0] is not None:
        v = row[0]
        if isinstance(v, (datetime, date)):
            return v.isoformat() if isinstance(v, datetime) else v.isoformat()
        return str(v)
    return None


def _filter_stocks_needing_update(
    db: Database, table: str, col: str, stock_ids: list[str],
    threshold: str,
) -> tuple[list[str], list[str]]:
    """Split stock_ids into (need_update, up_to_date) based on MAX(col) >= threshold.

    threshold is an ISO date string. A stock is considered up-to-date if its
    latest data in `table.col` is >= threshold.
    """
    validate_table_name(table)
    need_update: list[str] = []
    up_to_date: list[str] = []
    if not stock_ids:
        return need_update, up_to_date
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT stock_id, MAX({col}) FROM {table} WHERE stock_id = ANY(?) GROUP BY stock_id",
            [stock_ids]
        ).fetchall()
    latest_map = {r[0]: str(r[1]) if r[1] is not None else None for r in rows}
    for sid in stock_ids:
        latest = latest_map.get(sid)
        if latest and latest >= threshold:
            up_to_date.append(sid)
        else:
            need_update.append(sid)
    return need_update, up_to_date


def _estimate_announcement_date(d: str) -> str:
    dt = pd.Timestamp(d)
    month = dt.month
    if month <= 3:
        offset = 75
    elif month <= 6:
        offset = 45
    elif month <= 9:
        offset = 45
    else:
        offset = 75
    return (dt + pd.Timedelta(days=offset)).strftime("%Y-%m-%d")


def _pivot_financials(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["type"].isin(FINANCIAL_TYPE_MAP)]
    if df.empty:
        return pd.DataFrame()
    df["column"] = df["type"].map(FINANCIAL_TYPE_MAP)
    pivoted = df.pivot_table(
        index=["stock_id", "date"],
        columns="column",
        values="value",
        aggfunc="first",
    ).reset_index()
    pivoted.columns.name = None

    # Ensure all columns exist
    for col in FINANCIAL_TYPE_MAP.values():
        if col not in pivoted.columns:
            pivoted[col] = np.nan

    pivoted["year_quarter"] = pivoted["date"].apply(_date_to_year_quarter)
    pivoted["announcement_date"] = pivoted["date"].apply(_estimate_announcement_date)
    if "revenue" in pivoted.columns and pivoted["revenue"].notna().any():
        if "gross_profit" in pivoted.columns:
            pivoted["gross_margin"] = pivoted["gross_profit"] / pivoted["revenue"].replace(0, np.nan)
        if "operating_income" in pivoted.columns:
            pivoted["operating_margin"] = pivoted["operating_income"] / pivoted["revenue"].replace(0, np.nan)
    else:
        pivoted["gross_margin"] = np.nan
        pivoted["operating_margin"] = np.nan
    return pivoted


def _pivot_balance_sheet(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["type"].isin(BALANCE_SHEET_TYPE_MAP)]
    if df.empty:
        return pd.DataFrame()
    df["column"] = df["type"].map(BALANCE_SHEET_TYPE_MAP)
    pivoted = df.pivot_table(
        index=["stock_id", "date"],
        columns="column",
        values="value",
        aggfunc="first",
    ).reset_index()
    pivoted.columns.name = None

    # Ensure all columns exist
    for col in BALANCE_SHEET_TYPE_MAP.values():
        if col not in pivoted.columns:
            pivoted[col] = np.nan

    return pivoted


def _pivot_cash_flows(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df[df["type"].isin(CASH_FLOW_TYPE_MAP)]
    if df.empty:
        return pd.DataFrame()
    df["column"] = df["type"].map(CASH_FLOW_TYPE_MAP)
    pivoted = df.pivot_table(
        index=["stock_id", "date"],
        columns="column",
        values="value",
        aggfunc="first",
    ).reset_index()
    pivoted.columns.name = None

    for col in CASH_FLOW_TYPE_MAP.values():
        if col not in pivoted.columns:
            pivoted[col] = np.nan

    return pivoted


def update_daily_prices(db: Database, client: FinMindClient, stock_ids: list[str], start: date, end: date):
    # Skip stocks that already have data through yesterday (one day buffer for settlement)
    threshold = (end - timedelta(days=1)).isoformat()
    need, skipped = _filter_stocks_needing_update(db, "daily_prices", "trade_date", stock_ids, threshold)
    if skipped:
        log.info("ingestion.daily_prices.skip_uptodate", count=len(skipped))
    total = 0
    for sid in need:
        try:
            raw = client.get_daily_prices(sid, start, end)
            rows = [{PRICE_COLUMNS.get(k, k): v for k, v in r.items() if k in PRICE_COLUMNS} for r in raw]
            if not rows:
                with db.connection() as conn:
                    update_tracker_for_stock(conn, sid, "price", "ok")
                    conn.commit()
                continue
            with db.connection() as conn:
                n = _upsert(conn, "daily_prices", rows, ["stock_id", "trade_date"])
                update_tracker_for_stock(conn, sid, "price", "ok")
                conn.commit()
            total += n
        except FinMindRateLimitError as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "price", "rate_limited", str(e))
                conn.commit()
            log.warning("ingestion.daily_prices.rate_limited", sid=sid, error=str(e))
            raise
        except Exception as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "price", "failed", str(e))
                conn.commit()
            log.warning("ingestion.daily_prices.stock_failed", sid=sid, error=str(e))
            continue
    log.info("ingestion.daily_prices", stocks=len(need), skipped=len(skipped), rows=total)
    return total


def update_daily_prices_from_twse(db: Database) -> tuple[int, str]:
    """Update daily_prices using TWSE STOCK_DAY_ALL as primary source.
    
    Uses TTL cache (30 min) to avoid redundant HTTP requests.
    Returns (rows_written, trade_date_iso).
    """
    rows = _fetch_with_cache(fetch_twse_daily_prices_all, "fetch_twse_daily_prices_all")
    if not rows:
        return 0, ""
    trade_date = rows[0]["trade_date"]
    with db.connection() as conn:
        n = _upsert(conn, "daily_prices", rows, ["stock_id", "trade_date"])
        conn.commit()
    log.info("ingestion.daily_prices.twse", rows=n, date=trade_date)
    gc.collect()
    return n, trade_date


def update_valuations(db: Database, client: FinMindClient, stock_ids: list[str], start: str, end: str):
    # For daily-evaluated data, skip if already up to end-1 day
    threshold = (pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    need, skipped = _filter_stocks_needing_update(db, "valuations", "trade_date", stock_ids, threshold)
    if skipped:
        log.info("ingestion.valuations.skip_uptodate", count=len(skipped))
    total = 0
    for sid in need:
        try:
            raw = client.get_per_pbr(sid, start, end)
            rows = [{VALUATION_COLUMNS.get(k, k): v for k, v in r.items() if k in VALUATION_COLUMNS} for r in raw]
            if not rows:
                with db.connection() as conn:
                    update_tracker_for_stock(conn, sid, "per", "ok")
                    conn.commit()
                continue
            with db.connection() as conn:
                n = _upsert(conn, "valuations", rows, ["stock_id", "trade_date"])
                update_tracker_for_stock(conn, sid, "per", "ok")
                conn.commit()
            total += n
        except FinMindRateLimitError as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "per", "rate_limited", str(e))
                conn.commit()
            log.warning("ingestion.valuations.rate_limited", sid=sid, error=str(e))
            raise
        except Exception as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "per", "failed", str(e))
                conn.commit()
            log.warning("ingestion.valuations.stock_failed", sid=sid, error=str(e))
            continue
    log.info("ingestion.valuations", stocks=len(need), skipped=len(skipped), rows=total)
    return total


def update_valuations_from_twse(db: Database) -> tuple[int, int]:
    """Update valuations using TWSE BWIBBU_ALL as primary source.
    
    Uses TTL cache (30 min) to avoid redundant HTTP requests.
    Fetches PE ratio, PB ratio, dividend yield for all TWSE stocks.
    Then computes market_cap from latest close * shares outstanding.
    Returns (valuations_upserted, market_cap_computed).
    """
    rows, shares_map = _fetch_with_cache(fetch_twse_valuations_all, "fetch_twse_valuations_all")
    if not rows:
        return 0, 0

    bwibbu_date = rows[0]["trade_date"]
    with db.connection() as conn:
        n = _upsert(conn, "valuations", rows, ["stock_id", "trade_date"])

        market_cap_count = 0
        for sid, shares in shares_map.items():
            close_r = conn.execute(
                "SELECT close FROM daily_prices WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 1",
                [sid]
            ).fetchone()
            if close_r and close_r[0]:
                mc = close_r[0] * shares
                conn.execute(
                    "UPDATE valuations SET market_cap = ? WHERE stock_id = ? AND trade_date = ?",
                    [mc, sid, bwibbu_date]
                )
                market_cap_count += 1
        conn.commit()

    log.info("ingestion.valuations.twse",
             rows=n, market_cap=market_cap_count, date=bwibbu_date)
    gc.collect()
    return n, market_cap_count


def update_monthly_revenue_from_twse(db: Database) -> int:
    """Update monthly_revenue using TWSE t187ap05_L as primary source.
    
    Uses TTL cache (30 min) to avoid redundant HTTP requests.
    """
    rows = _fetch_with_cache(fetch_twse_revenue_all, "fetch_twse_revenue_all")
    if not rows:
        return 0
    with db.connection() as conn:
        # Filter to stocks that exist in the stocks table to avoid FK violations
        existing = {r[0] for r in conn.execute("SELECT stock_id FROM stocks").fetchall()}
        filtered = [r for r in rows if r["stock_id"] in existing]
        skipped = len(rows) - len(filtered)
        if skipped:
            log.warning("ingestion.revenue.skip_missing_stocks", skipped=skipped)
        n = _upsert(conn, "monthly_revenue", filtered, ["stock_id", "year_month"])
        conn.commit()
    log.info("ingestion.revenue.twse", rows=n, year_month=rows[0].get("year_month") if rows else "N/A")
    return n


def update_monthly_revenue(db: Database, client: FinMindClient, stock_ids: list[str], start: str, end: str):
    # year_month format YYYYMM. Threshold = previous month (e.g. 202604 for "end=2026-05-15")
    end_dt = pd.Timestamp(end)
    threshold_ym = f"{end_dt.year}{(end_dt.month - 1) if end_dt.month > 1 else 12:02d}"
    if end_dt.month == 1:
        threshold_ym = f"{end_dt.year - 1}12"
    need, skipped = _filter_stocks_needing_update(db, "monthly_revenue", "year_month", stock_ids, threshold_ym)
    if skipped:
        log.info("ingestion.monthly_revenue.skip_uptodate", count=len(skipped), threshold=threshold_ym)
    total = 0
    for sid in need:
        try:
            raw = client.get_monthly_revenue(sid, start, end)
            rows = []
            for r in raw:
                row = {}
                for k, v in r.items():
                    target = {"year_month": "year_month"}.get(k, k)
                    row[target] = v
                rev_cols = {"stock_id", "revenue_month", "revenue", "date", "announcement_date"}
                row = {k: v for k, v in row.items() if k in rev_cols}
                if "year_month" not in row and "revenue_month" in r:
                    row["year_month"] = r["revenue_month"]
                if "announcement_date" not in row and "date" in r:
                    row["announcement_date"] = r["date"]
                rows.append(row)

            if rows:
                rev_df = pd.DataFrame(rows)
                rev_df["revenue"] = pd.to_numeric(rev_df["revenue"], errors="coerce")
                rev_df["year_month"] = rev_df["year_month"].astype(str)
                rev_df = rev_df.sort_values(["stock_id", "year_month"])
                rev_df["revenue_prev"] = rev_df.groupby("stock_id")["revenue"].shift(12)
                rev_df["revenue_yoy"] = (rev_df["revenue"] / rev_df["revenue_prev"]) - 1
                rev_df["revenue_yoy"] = rev_df["revenue_yoy"].replace([np.inf, -np.inf], None)
                rev_df = rev_df[rev_df["revenue_yoy"].notna()]
                result = rev_df[["stock_id", "year_month", "revenue", "revenue_yoy", "announcement_date"]].to_dict("records")
                with db.connection() as conn:
                    n = _upsert(conn, "monthly_revenue", result, ["stock_id", "year_month"])
                    update_tracker_for_stock(conn, sid, "revenue", "ok")
                    conn.commit()
                total += n
            else:
                with db.connection() as conn:
                    update_tracker_for_stock(conn, sid, "revenue", "ok")
                    conn.commit()
        except FinMindRateLimitError as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "revenue", "rate_limited", str(e))
                conn.commit()
            log.warning("ingestion.monthly_revenue.rate_limited", sid=sid, error=str(e))
            raise
        except Exception as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "revenue", "failed", str(e))
                conn.commit()
            log.warning("ingestion.monthly_revenue.stock_failed", sid=sid, error=str(e))
            continue
    log.info("ingestion.monthly_revenue", stocks=len(need), skipped=len(skipped), rows=total)
    return total


def update_financials(db: Database, client: FinMindClient, stock_ids: list[str], start: str, end: str):
    # year_quarter format YYYYQN. Threshold = previous quarter
    end_dt = pd.Timestamp(end)
    prev_q = (end_dt.month - 1) // 3
    if prev_q == 0:
        threshold_yq = f"{end_dt.year - 1}Q4"
    else:
        threshold_yq = f"{end_dt.year}Q{prev_q}"
    need, skipped = _filter_stocks_needing_update(db, "financials", "year_quarter", stock_ids, threshold_yq)
    if skipped:
        log.info("ingestion.financials.skip_uptodate", count=len(skipped), threshold=threshold_yq)
    total = 0
    for sid in need:
        try:
            fin_raw = client.get_financials(sid, start, end)
            bs_raw = client.get_balance_sheet(sid, start, end)
            cf_raw = client.get_cash_flows(sid, start, end)

            fin_df = _pivot_financials(fin_raw)
            bs_df = _pivot_balance_sheet(bs_raw)
            cf_df = _pivot_cash_flows(cf_raw)

            if fin_df.empty:
                with db.connection() as conn:
                    update_tracker_for_stock(conn, sid, "financials", "ok")
                    conn.commit()
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

            for num, den, col in [
                ("net_income", "equity", "roe"),
                ("net_income", "total_assets", "roa"),
                ("liabilities", "equity", "debt_to_equity"),
            ]:
                if den in merged.columns and merged[den].notna().any():
                    merged[col] = merged[num] / merged[den].replace(0, pd.NA)
                else:
                    merged[col] = pd.NA

            # Compute current_ratio = current_assets / current_liabilities
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
                n = _upsert(conn, "financials", result, ["stock_id", "year_quarter"])
                update_tracker_for_stock(conn, sid, "financials", "ok")
                conn.commit()
            total += n
        except FinMindRateLimitError as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "financials", "rate_limited", str(e))
                conn.commit()
            log.warning("ingestion.financials.rate_limited", sid=sid, error=str(e))
            raise
        except Exception as e:
            with db.connection() as conn:
                update_tracker_for_stock(conn, sid, "financials", "failed", str(e))
                conn.commit()
            log.warning("ingestion.financials.stock_failed", sid=sid, error=str(e))
            continue
    log.info("ingestion.financials", stocks=len(need), skipped=len(skipped), rows=total)
    return total


INSTITUTIONAL_COLUMNS = frozenset({
    "stock_id", "trade_date", "market",
    "foreign_investors_net", "sity_investors_net", "dealer_net",
    "dealer_proprietary_net", "dealer_hedge_net", "total_net",
})


def update_institutional_flows_from_twse(
    db: Database,
    trade_date: Optional[str] = None,
    retry: bool = False,
    max_retries: int = 3,
    retry_delay_seconds: int = 1800,
) -> int:
    """Update institutional_flows using TWSE T86 API as primary source.

    Args:
        db: Database instance.
        trade_date: ISO date string (default: today).
        retry: Enable retry + trading day check for realtime use.
        max_retries: Max retry attempts (only used when *retry* is True).
        retry_delay_seconds: Delay between retries in seconds.

    Returns:
        Number of rows upserted.
    """
    _date = trade_date or date.today().isoformat()

    if retry:
        if not is_trading_day(_date):
            log.info("ingestion.institutional.twse.not_trading_day", date=_date)
            return 0
        rows = fetch_with_retry(
            fetch_twse_institutional_all,
            _date,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            fn_name="fetch_twse_institutional_all",
        )
    else:
        rows = fetch_twse_institutional_all(_date)

    if not rows:
        log.warning("ingestion.institutional.twse.no_data", date=_date)
        return 0

    # Filter out stock_ids not in stocks table (e.g. ETF codes)
    existing = {r[0] for r in db.execute("SELECT stock_id FROM stocks").fetchall()}
    valid = [r for r in rows if r["stock_id"] in existing]
    skipped = len(rows) - len(valid)
    if skipped:
        log.info("ingestion.institutional.twse.skipped_unknown", count=skipped)

    actual_date = rows[0]["trade_date"]
    with db.connection() as conn:
        n = _upsert(conn, "institutional_flows", valid, ["stock_id", "trade_date"])
        conn.commit()

    log.info("ingestion.institutional.twse", rows=n, date=actual_date)
    return n


def update_institutional_flows_from_tpex(
    db: Database,
    trade_date: Optional[str] = None,
    retry: bool = False,
    max_retries: int = 3,
    retry_delay_seconds: int = 1800,
) -> int:
    """Update institutional_flows using TPEX Open API as supplementary source.

    Args:
        db: Database instance.
        trade_date: ISO date string (default: today).
        retry: Enable retry + trading day check for realtime use.
        max_retries: Max retry attempts.
        retry_delay_seconds: Delay between retries in seconds.

    Returns:
        Number of rows upserted.
    """
    _date = trade_date or date.today().isoformat()

    if retry:
        if not is_trading_day(_date):
            log.info("ingestion.institutional.tpex.not_trading_day", date=_date)
            return 0
        rows = fetch_with_retry(
            fetch_tpex_institutional_all,
            _date,
            max_retries=max_retries,
            retry_delay_seconds=retry_delay_seconds,
            fn_name="fetch_tpex_institutional_all",
        )
    else:
        rows = fetch_tpex_institutional_all(_date)

    if not rows:
        log.warning("ingestion.institutional.tpex.no_data", date=_date)
        return 0

    # Filter out stock_ids that don't exist in the stocks table (e.g. ETF codes)
    existing = {
        r[0] for r in db.execute("SELECT stock_id FROM stocks").fetchall()
    }
    valid = [r for r in rows if r["stock_id"] in existing]
    skipped = len(rows) - len(valid)
    if skipped:
        log.info("ingestion.institutional.tpex.skipped_unknown", count=skipped)

    actual_date = rows[0]["trade_date"]
    with db.connection() as conn:
        n = _upsert(conn, "institutional_flows", valid, ["stock_id", "trade_date"])
        conn.commit()

    log.info("ingestion.institutional.tpex", rows=n, date=actual_date)
    return n
