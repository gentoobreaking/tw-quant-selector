import argparse
import json
import os, sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tw_quant_selector.data.database import Database
from tw_quant_selector.data.finmind_client import FinMindClient
from tw_quant_selector.data.scheduler import run_daily_update

PIPELINE_STATE_FILE = os.getenv("PIPELINE_STATE_FILE", "/tmp/pipeline_state.json")


def _apply_banned_state(client: FinMindClient) -> None:
    """Load pipeline state and set client ban if still within cooldown."""
    if not os.path.exists(PIPELINE_STATE_FILE):
        return
    try:
        state = json.loads(open(PIPELINE_STATE_FILE).read())
        failed_at = state.get("failed_at")
        retry_after = state.get("retry_after_minutes", 60)
        if not failed_at:
            return
        banned_until = datetime.fromisoformat(failed_at) + timedelta(minutes=retry_after)
        if banned_until > datetime.now():
            client.set_banned_until(banned_until)
            print(f"  ⏳ FinMind 仍在封鎖中，將於 {banned_until.strftime('%H:%M')} 解除")
    except Exception as e:
        print(f"  ⚠️ 讀取 pipeline state 失敗: {e}")

from tw_quant_selector.data.twstock_client import update_stock_list, MarketScope
from tw_quant_selector.strategies.combiner import compute_composite_scores

parser = argparse.ArgumentParser(description="Daily pipeline")
parser.add_argument("run_date", nargs="?", help="Date in YYYY-MM-DD (default: today)")
parser.add_argument(
    "--scope",
    choices=["TWSE", "TPEX", "ALL"],
    default=os.environ.get("STOCK_MARKET_SCOPE", "TWSE").upper(),
    help="Stock market scope (default: STOCK_MARKET_SCOPE env or TWSE)",
)
parser.add_argument(
    "--datasets",
    help=(
        "Comma-separated datasets to run. "
        "Valid: price,per,revenue,financials,institutional,holdings. "
        "Default = all. Example: --datasets price,revenue"
    ),
)
parser.add_argument(
    "--resume-rate-limited",
    action="store_true",
    help="On startup, check for a rate-limited pipeline state and resume those stocks only.",
)
parser.add_argument(
    "--prioritize-holdings",
    action=argparse.BooleanOptionalAction,
    default=None,
    help=(
        "Prioritize stocks currently held in the portfolio table. "
        "Holdings (shares > 0) are processed first / exclusively, ensuring "
        "stock-detail pages and guru filters always have fresh data for stocks you own. "
        "Default (omit both): auto-detect — prioritize if any holdings exist, else fall back to bucket. "
        "Use --no-prioritize-holdings to force bucket-only."
    ),
)
parser.add_argument("token", nargs="?", help="FinMind API token (or set FINMIND_TOKEN env)")
args = parser.parse_args()


token = args.token or os.environ.get("FINMIND_TOKEN", "")
if not token:
    print("Usage: FINMIND_TOKEN=xxx python scripts/run_daily_pipeline.py [DATE] [--scope TWSE|TPEX|ALL] [--datasets price,revenue]")
    sys.exit(1)

datasets = None
if args.datasets:
    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]

run_date = date.today()
if args.run_date:
    try:
        run_date = date.fromisoformat(args.run_date)
    except ValueError:
        pass

db = Database()
client = FinMindClient(token)
_apply_banned_state(client)

print(f"📋 Step 0: Sync stock list from twstock.codes")
n_stocks = update_stock_list(db)
print(f"  {n_stocks} stocks in DB (scope={args.scope})")

print(f"🏭 Step 1: Ingest data for {run_date}")
ingest = run_daily_update(db, client, run_date, datasets=datasets, prioritize_holdings=args.prioritize_holdings)
if ingest.get("status") == "skipped":
    print(f"  ⏭ {ingest.get('reason')} — make sure stocks table is populated")
    db.close()
    sys.exit(0)

print(f"  stocks_in_batch: {ingest['stocks_in_batch']}")
for ds, n in ingest.get("datasets", {}).items():
    print(f"    {ds}: {n} rows")

if ingest.get("rate_limited"):
    print(f"\n🛑 Pipeline 被 FinMind rate-limit 中斷 (dataset={ingest['rate_limited']})")
    print(f"   1 小時後可重跑同指令自動從中斷處接續")
    print(f"   進度檔: /tmp/pipeline_state.json")
    db.close()
    sys.exit(75)  # EX_TEMPFAIL — wrapper scripts can detect and retry

print(f"\n🧮 Step 2: Compute composite scores")
result = compute_composite_scores(db, run_date)

print(f"\n📊 Top {len(result['stocks'])} Stocks:")
for s in result["stocks"]:
    print(f"  #{s['rank']:2d} {s['stock_id']:6s}  score={s['score']:.4f}")

print(f"\n📊 Top {len(result['etfs'])} ETFs:")
for s in result["etfs"]:
    print(f"  #{s['rank']:2d} {s['stock_id']:6s}  score={s['score']:.4f}")

print(f"\n✅ Done — {result['total_candidates']} candidates evaluated")

# --- Step 3: System Health Check ---
print(f"\n🔍 Step 3: Running system health check...")
try:
    from tw_quant_selector.monitoring.alerting import AlertChecker
    checker = AlertChecker(db)
    checker.check_all()
    print("  ✅ Health check completed")
except Exception as e:
    print(f"  ⚠️ Health check failed: {e}")

db.close()
