"""
Usage: FINMIND_TOKEN=xxx python scripts/run_demo.py
"""
import os, sys
from datetime import date, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tw_quant_selector.data.database import Database
from tw_quant_selector.data.finmind_client import FinMindClient
from tw_quant_selector.data.ingestion import (
    update_daily_prices,
    update_valuations,
    update_monthly_revenue,
    update_financials,
)
from tw_quant_selector.data.adjustment import compute_dividend_adjustments, apply_all_adjustments
from tw_quant_selector.backtest.engine import run_backtest

# PostgreSQL demo: uses default schema (public)
db = Database()

stock_ids = ["2330", "2317", "2454", "2308", "2412", "2303",
             "2002", "1301", "1326", "1216", "1101", "3008",
             "2357", "2382", "4904"]
etf_ids = ["0050", "0056", "00878"]
all_ids = stock_ids + etf_ids

token = os.environ.get("FINMIND_TOKEN", "")
if not token and len(sys.argv) > 1:
    token = sys.argv[1]
if not token:
    print("Usage: FINMIND_TOKEN=xxx python scripts/run_demo.py")
    sys.exit(1)

client = FinMindClient(token)

with db.connection() as conn:
    for sid in stock_ids:
        conn.execute("INSERT INTO stocks (stock_id, stock_name, market, industry, list_date, is_etf, created_at) VALUES (:sid, :name, 'TSE', '科技', '2000-01-01', FALSE, NOW()) ON CONFLICT (stock_id) DO NOTHING",
                     {"sid": sid, "name": sid})
    for sid in etf_ids:
        conn.execute("INSERT INTO stocks (stock_id, stock_name, market, industry, list_date, is_etf, created_at) VALUES (:sid, :name, 'TSE', 'ETF', '2015-01-01', TRUE, NOW()) ON CONFLICT (stock_id) DO NOTHING",
                     {"sid": sid, "name": sid})
    conn.commit()

today_iso = date.today().isoformat()

print("📥 Fetching daily prices from FinMind...")
update_daily_prices(db, client, all_ids, date(2023, 1, 2), date.today())

print("📥 Fetching valuations (PER/PBR)...")
update_valuations(db, client, all_ids, "2023-01-02", today_iso)

print("📥 Fetching monthly revenue...")
update_monthly_revenue(db, client, all_ids, "2020-01-01", today_iso)

print("📥 Fetching financial statements & balance sheets...")
update_financials(db, client, all_ids, "2022-01-01", today_iso)

print("📥 Fetching dividends & computing adjustments...")
with db.connection() as conn:
    for sid in all_ids:
        divs = client.get_dividend(sid, "2022-01-01", today_iso)
        if divs:
            adjustments = compute_dividend_adjustments(db, sid, divs)
            for adj in adjustments:
                conn.execute(
                    "UPDATE daily_prices SET adj_factor = :f WHERE stock_id = :sid AND trade_date = :dt",
                    {"f": adj["adj_factor"], "sid": sid, "dt": adj["date"]}
                )
    conn.commit()

print("📐 Applying cumulative dividend adjustments...")
apply_all_adjustments(db)

print("🧮 Computing market cap from prices & shares outstanding...")
with db.connection() as conn:
    for sid in stock_ids:
        bs = client.get_balance_sheet(sid, "2024-01-01", today_iso)
        shares = None
        for r in bs:
            if r.get("type") == "OrdinaryShare" and r.get("value"):
                shares = float(r["value"]) / 10
                break
        if not shares:
            continue
        conn.execute("""
            UPDATE valuations v
            SET market_cap = ROUND(dp.close * ?, 2)
            FROM daily_prices dp
            WHERE v.stock_id = dp.stock_id
              AND v.trade_date = dp.trade_date
              AND v.stock_id = ? AND v.market_cap IS NULL
        """, {"shares": shares, "sid": sid})
    conn.commit()

print("\n🚀 Running backtest 2024-04 ~ 2025-01...")
metrics = run_backtest(db, date(2024, 4, 1), date(2025, 1, 1))

print(f"\n📊 Backtest Results:")
for k, v in metrics.items():
    if k not in ("run_id", "start_date", "end_date", "strategy_config", "max_drawdown_start", "max_drawdown_end"):
        print(f"  {k}: {v}")

db.close()
