#!/usr/bin/env python3
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Add project root and src to path
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from tw_quant_selector.data.database import Database
from tw_quant_selector.data.realtime_quotes import poll_realtime, save_intraday_snapshot, is_market_open, is_trading_day

def main():
    print("🚀 Real-time Polling Daemon Started")
    db = Database()
    
    # Get stocks to poll: holdings + top picks
    with db.connection() as conn:
        # Holdings
        rows = conn.execute("SELECT stock_id FROM portfolio WHERE shares > 0").fetchall()
        holdings = [r[0] for r in rows]
        
        # Top signals (composite strategy)
        rows = conn.execute("""
            SELECT stock_id FROM signals 
            WHERE strategy = 'composite' AND signal_date = (SELECT MAX(signal_date) FROM signals)
            ORDER BY score DESC LIMIT 50
        """).fetchall()
        picks = [r[0] for r in rows]
        
    all_stocks = list(set(holdings + picks + ["0050", "2330"]))
    print(f"🔍 Monitoring {len(all_stocks)} stocks (Holdings: {len(holdings)}, Picks: {len(picks)})")

    last_snapshot_time = 0
    snapshot_interval = 300 # 5 minutes

    while True:
        now = datetime.now()
        
        if not is_trading_day(now.date()):
            print(f"😴 {now.strftime('%H:%M:%S')} Weekend - sleeping...")
            time.sleep(3600)
            continue
            
        if not is_market_open(now):
            print(f"😴 {now.strftime('%H:%M:%S')} Market closed - sleeping...")
            time.sleep(300)
            continue
            
        print(f"🔄 {now.strftime('%H:%M:%S')} Polling quotes...")
        res = poll_realtime(db, all_stocks)
        if res.get("status") == "ok":
            print(f"  ✅ Saved {res.get('count')} quotes")
        else:
            print(f"  ⚠️ Polling skipped or failed: {res.get('reason') or res.get('error')}")

        # Save intraday snapshot every 5 minutes
        if time.time() - last_snapshot_time > snapshot_interval:
            print(f"📸 {now.strftime('%H:%M:%S')} Saving intraday snapshots...")
            s_res = save_intraday_snapshot(db, all_stocks)
            if s_res.get("status") == "ok":
                print(f"  ✅ Saved {s_res.get('count')} snapshots")
                last_snapshot_time = time.time()
            else:
                print(f"  ⚠️ Snapshot failed: {s_res.get('error')}")

        time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 Stopped.")
