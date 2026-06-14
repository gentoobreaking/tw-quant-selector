"""
Seed guru_scores table with Piotroski F-Score (0-9).

Computes 9 binary criteria across 3 categories (Profitability, Funding,
Operating Efficiency) for each stock, then writes results to the
guru_scores table with guru='piotroski'.

Usage:
    python -m scripts.seed_guru_scores                   # default: last Friday
    python -m scripts.seed_guru_scores --date 2025-12-31
"""
import argparse
import json
import os
import sys
from datetime import date, timedelta

import pandas as pd
import structlog
from sqlalchemy import text

from tw_quant_selector.data.database import get_session
from tw_quant_selector.data.finmind_client import FinMindClient
from tw_quant_selector.data.ingestion import _pivot_financials, _pivot_balance_sheet, _pivot_cash_flows

log = structlog.get_logger()


def _to_float(v):
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _get_last_friday() -> date:
    today = date.today()
    offset = (today.weekday() - 4) % 7
    return today - timedelta(days=offset)


def _get_annual_data_for_stock(
    sid: str, year: int, client: FinMindClient
) -> dict | None:
    """Aggregate the 4 quarters of year *year* into annual totals.

    Returns dict with keys matching F-Score needs or None if insufficient data.
    """
    start = f"{year}-01-01"
    end = f"{year}-12-31"

    try:
        fin_raw = client.get_financials(sid, start, end)
        bs_raw = client.get_balance_sheet(sid, start, end)
        cf_raw = client.get_cash_flows(sid, start, end)
    except Exception:
        return None

    if not fin_raw:
        return None

    fin_df = _pivot_financials(fin_raw)
    if fin_df.empty:
        return None

    bs_df = _pivot_balance_sheet(bs_raw) if bs_raw else pd.DataFrame()
    cf_df = _pivot_cash_flows(cf_raw) if cf_raw else pd.DataFrame()

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

    # Compute derived ratios
    for num_col, den_col, out_col in [
        ("net_income", "equity", "roe"),
        ("net_income", "total_assets", "roa"),
        ("liabilities", "equity", "debt_to_equity"),
    ]:
        if den_col in merged.columns and merged[den_col].notna().any():
            safe_den = merged[den_col].replace(0, pd.NA)
            merged[out_col] = merged[num_col] / safe_den
        else:
            merged[out_col] = pd.NA

    # Gross margin
    if "gross_profit" in merged.columns and "revenue" in merged.columns:
        safe_rev = merged["revenue"].replace(0, pd.NA)
        merged["gross_margin"] = merged["gross_profit"] / safe_rev

    # Asset turnover (revenue / total_assets)
    if "total_assets" in merged.columns:
        safe_ta = merged["total_assets"].replace(0, pd.NA)
        merged["asset_turnover"] = merged["revenue"] / safe_ta

    # Current ratio (current_assets / current_liabilities)
    if "current_assets" in merged.columns and "current_liabilities" in merged.columns:
        safe_cl = merged["current_liabilities"].replace(0, pd.NA)
        merged["current_ratio"] = merged["current_assets"] / safe_cl

    # Fallback: operating_cash_flow → net_income if missing
    if "operating_cash_flow" in merged.columns:
        merged["operating_cash_flow"] = merged["operating_cash_flow"].fillna(merged["net_income"])

    # Aggregate to annual
    annual = {}
    for col in ["revenue", "gross_profit", "operating_income", "net_income"]:
        if col in merged.columns:
            annual[col] = float(merged[col].sum()) if merged[col].notna().any() else None

    # Averages for ratios
    for col in ["roa", "gross_margin", "debt_to_equity", "roe", "asset_turnover", "current_ratio"]:
        if col in merged.columns:
            vals = merged[col].dropna()
            annual[col] = float(vals.mean()) if not vals.empty else None

    # Operating cash flow (sum, same as income)
    if "operating_cash_flow" in merged.columns:
        annual["operating_cash_flow"] = float(merged["operating_cash_flow"].sum()) if merged["operating_cash_flow"].notna().any() else None

    annual["total_assets"] = (
        float(merged["total_assets"].dropna().iloc[-1])
        if "total_assets" in merged.columns and merged["total_assets"].notna().any()
        else None
    )
    annual["equity"] = (
        float(merged["equity"].dropna().iloc[-1])
        if "equity" in merged.columns and merged["equity"].notna().any()
        else None
    )
    annual["liabilities"] = (
        float(merged["liabilities"].dropna().iloc[-1])
        if "liabilities" in merged.columns and merged["liabilities"].notna().any()
        else None
    )

    return annual


def calculate_piotroski_f_score(
    sid: str, as_of_date: date, client: FinMindClient
) -> tuple[int, dict[str, bool]]:
    """Compute Piotroski F-Score (0-9) for a single stock.

    Returns (f_score, component_scores).
    """
    fy_this = as_of_date.year if as_of_date.month > 3 else as_of_date.year - 1

    this_year = _get_annual_data_for_stock(sid, fy_this, client)
    last_year = _get_annual_data_for_stock(sid, fy_this - 1, client)

    def _g(key):
        return (this_year or {}).get(key)

    def _l(key):
        return (last_year or {}).get(key)

    criteria: dict[str, object] = {}

    # ── Profitability (4 pts) ──────────────────────────
    # 1. ROA > 0
    roa_val = _to_float(_g("roa"))
    criteria["roa_positive"] = bool(roa_val is not None and roa_val > 0)
    criteria["roa_value"] = roa_val
    criteria["roa_threshold"] = 0

    # 2. Operating CF > 0
    cfo_val = _to_float(_g("operating_cash_flow"))
    criteria["cf_positive"] = bool(cfo_val is not None and cfo_val > 0)
    criteria["cf_label"] = "Operating Cash Flow" if cfo_val is not None else "Net Income（替代）"
    criteria["cf_value"] = cfo_val
    criteria["cf_threshold"] = 0

    # 3. ΔROA > 0
    roa_last = _to_float(_l("roa"))
    criteria["delta_roa_positive"] = bool(
        roa_val is not None and roa_last is not None and roa_val > roa_last
    )
    criteria["delta_roa_value"] = roa_val - roa_last if (roa_val is not None and roa_last is not None) else None
    criteria["delta_roa_last"] = roa_last
    criteria["delta_roa_threshold"] = 0

    # 4. Accruals < 0 (Accruals = Net Income - Operating CF)
    ni_val = _to_float(_g("net_income"))
    cf_for_accrual = cfo_val if cfo_val is not None else ni_val
    accrual_val = (ni_val - cf_for_accrual) if (ni_val is not None and cf_for_accrual is not None) else None
    criteria["accruals_negative"] = bool(accrual_val is not None and accrual_val < 0)
    criteria["accruals_label"] = "Accruals (NI - CFO)" if cfo_val is not None else "Net Income > 0（替代）"
    criteria["accruals_value"] = accrual_val
    criteria["accruals_threshold"] = 0

    # ── Funding (3 pts) ────────────────────────────────
    # 5. ΔLeverage < 0 (debt_to_equity decreased)
    dte_val = _to_float(_g("debt_to_equity"))
    dte_last = _to_float(_l("debt_to_equity"))
    criteria["delta_leverage_negative"] = bool(
        dte_val is not None and dte_last is not None and dte_val < dte_last
    )
    criteria["delta_leverage_value"] = dte_val
    criteria["delta_leverage_last"] = dte_last
    criteria["delta_leverage_threshold"] = "current < prior"

    # 6. ΔCurrent Ratio > 0
    cr_val = _to_float(_g("current_ratio"))
    cr_last = _to_float(_l("current_ratio"))
    criteria["delta_current_ratio_positive"] = bool(
        cr_val is not None and cr_last is not None and cr_val > cr_last
    )
    criteria["delta_current_ratio_label"] = "Current Ratio" if cr_val is not None else "（尚無資料）"
    criteria["delta_current_ratio_value"] = cr_val
    criteria["delta_current_ratio_last"] = cr_last

    # 7. No new shares (not available from current ingestion; assume True)
    criteria["no_new_shares"] = True
    criteria["no_new_shares_label"] = "（尚無資料，預設通過）"

    # ── Operating Efficiency (2 pts) ────────────────────
    # 8. ΔGross Margin > 0
    gm_val = _to_float(_g("gross_margin"))
    gm_last = _to_float(_l("gross_margin"))
    criteria["delta_gross_margin_positive"] = bool(
        gm_val is not None and gm_last is not None and gm_val > gm_last
    )
    criteria["delta_gross_margin_value"] = gm_val
    criteria["delta_gross_margin_last"] = gm_last
    criteria["delta_gross_margin_threshold"] = 0

    # 9. ΔAsset Turnover > 0 (Revenue / Total Assets)
    at_val = _to_float(_g("asset_turnover"))
    at_last = _to_float(_l("asset_turnover"))
    criteria["delta_asset_turnover_positive"] = bool(
        at_val is not None and at_last is not None and at_val > at_last
    )
    criteria["delta_asset_turnover_value"] = at_val
    criteria["delta_asset_turnover_last"] = at_last
    criteria["delta_asset_turnover_threshold"] = 0

    f_score = sum(1 for k in ["roa_positive", "cf_positive", "delta_roa_positive",
                              "accruals_negative", "delta_leverage_negative",
                              "delta_current_ratio_positive", "no_new_shares",
                              "delta_gross_margin_positive", "delta_asset_turnover_positive"]
                  if criteria.get(k))
    return f_score, criteria


def calculate_all_guru_scores(
    as_of_date: date, client: FinMindClient, db_session
) -> list[dict]:
    """Compute Piotroski F-Score for all listed stocks.

    Returns list of dicts ready for guru_scores table insertion.
    """
    stocks = db_session.execute(
        text("SELECT stock_id FROM stocks ORDER BY stock_id")
    ).fetchall()

    results = []
    for (sid,) in stocks:
        try:
            score, criteria = calculate_piotroski_f_score(sid, as_of_date, client)
            results.append({
                "stock_id": sid,
                "score_date": as_of_date,
                "guru": "piotroski",
                "score": score,
                "pass_filter": score >= 7,
                "criteria_detail": criteria,
            })
            log.info("guru.piotroski.ok", stock_id=sid, f_score=score)
        except Exception:
            log.warning("guru.piotroski.failed", stock_id=sid, exc_info=True)
            continue

    return results


def save_guru_scores(rows: list[dict], db_session):
    """Upsert guru_scores rows (ON CONFLICT DO UPDATE)."""
    if not rows:
        log.info("guru.save.no_data")
        return

    for row in rows:
        db_session.execute(
            text("""
                INSERT INTO guru_scores (score_date, stock_id, guru, score, pass_filter, criteria_detail)
                VALUES (:score_date, :stock_id, :guru, :score, :pass_filter, :criteria_detail::jsonb)
                ON CONFLICT (score_date, stock_id, guru)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    pass_filter = EXCLUDED.pass_filter,
                    criteria_detail = EXCLUDED.criteria_detail
            """),
            {
                "score_date": row["score_date"],
                "stock_id": row["stock_id"],
                "guru": row["guru"],
                "score": row["score"],
                "pass_filter": row["pass_filter"],
                "criteria_detail": json.dumps(row["criteria_detail"]),
            },
        )
    db_session.commit()
    log.info("guru.save.complete", count=len(rows))


def seed_guru_scores(as_of_date: date | None = None):
    """Main entry point: calculate and save Piotroski F-Scores."""
    if as_of_date is None:
        as_of_date = _get_last_friday()

    token = os.getenv("FINMIND_TOKEN")
    if not token:
        log.error("guru.missing_finmind_token")
        sys.exit(1)

    client = FinMindClient(token=token)
    session = get_session()

    try:
        log.info("guru.start", as_of_date=as_of_date.isoformat())
        rows = calculate_all_guru_scores(as_of_date, client, session)
        save_guru_scores(rows, session)
    finally:
        session.close()
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Piotroski F-Score")
    parser.add_argument("--date", type=str, default=None, help="As-of date (YYYY-MM-DD)")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.date) if args.date else None
    seed_guru_scores(as_of)
