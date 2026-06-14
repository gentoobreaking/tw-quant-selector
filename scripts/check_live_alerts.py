#!/usr/bin/env python3
import json
import os
import sys
import httpx
import time
from pathlib import Path
from decimal import Decimal

# Add project root and src to path
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from tw_quant_selector.data.database import Database
from tw_quant_selector.monitoring.alerting import AlertManager, get_alert_config

TWSE_API_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

# Realtime price configuration
REALTIME_PRICE_ENABLED = os.environ.get("REALTIME_PRICE_ENABLED", "true").lower() == "true"
# REALTIME_DB_PATH no longer used (PostgreSQL uses a table instead)
REALTIME_DB_PATH = None
FASTAPI_NOTIFY_URL = "http://localhost:8000/api/v1/notify-realtime-update"

def fetch_live_prices(stocks: list[dict]) -> dict[str, float]:
    """Fetch live prices from TWSE MIS API."""
    if not stocks:
        return {}
    
    # Format: ex_ch=tse_2330.tw|otc_6244.tw
    channels = []
    for s in stocks:
        market_prefix = "otc" if s["market"] == "OTC" else "tse"
        channels.append(f"{market_prefix}_{s['stock_id']}.tw")
    
    params = {"ex_ch": "|".join(channels), "_": int(time.time() * 1000)}
    
    try:
        resp = httpx.get(TWSE_API_URL, params=params, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
        
        prices = {}
        for item in data.get("msgArray", []):
            sid = item.get("c")
            price_str = item.get("z") # current price
            if price_str == "-" or not price_str:
                price_str = item.get("y") # fallback to yesterday close if no trade yet
            
            if sid and price_str and price_str != "-":
                prices[sid] = float(price_str)
        return prices
    except Exception as e:
        print(f"❌ Error fetching TWSE prices: {e}")
        return {}

def write_realtime_prices_to_db(prices: dict[str, float]):
    """Write realtime prices to PostgreSQL realtime_prices table."""
    if not REALTIME_PRICE_ENABLED:
        return

    try:
        db = Database()
        with db.connection() as conn:
            # Create table if not exists
            conn.execute("""
                CREATE TABLE IF NOT EXISTS realtime_prices (
                    stock_id VARCHAR(10) PRIMARY KEY,
                    close DOUBLE PRECISION,
                    trade_date DATE,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Upsert prices
            for stock_id, price in prices.items():
                conn.execute("""
                    INSERT INTO realtime_prices (stock_id, close, trade_date)
                    VALUES (:sid, :price, CURRENT_DATE)
                    ON CONFLICT (stock_id) DO UPDATE SET
                        close = EXCLUDED.close,
                        trade_date = EXCLUDED.trade_date,
                        updated_at = CURRENT_TIMESTAMP
                """, {"sid": stock_id, "price": price})

        db.close()
        print(f"  ✅ Wrote {len(prices)} realtime prices to DB")

        # Notify FastAPI to trigger SSE event
        notify_realtime_update()

    except Exception as e:
        print(f"  ❌ Error writing realtime prices to DB: {e}")

def notify_realtime_update():
    """Notify FastAPI endpoint to trigger SSE event."""
    try:
        resp = httpx.post(FASTAPI_NOTIFY_URL, timeout=5.0)
        if resp.status_code == 200:
            print(f"  ✅ Notified FastAPI for SSE event")
        else:
            print(f"  ⚠️ FastAPI notify failed: {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️ Cannot notify FastAPI: {e}")

def check_live_alerts():
    monitor_path = ".stock_monitor.json"
    if not Path(monitor_path).exists():
        print(f"⚠️ {monitor_path} not found. Run scripts/export_portfolio.py first.")
        return

    with open(monitor_path, "r", encoding="utf-8") as f:
        holdings = json.load(f)

    if not holdings:
        print("ℹ️ No holdings to monitor.")
        return

    db = Database()
    manager = AlertManager(db)
    config = get_alert_config(db)
    
    # Check if realtime price writing is enabled
    if REALTIME_PRICE_ENABLED:
        print("  ℹ️ Realtime price writing is ENABLED")
    else:
        print("  ℹ️ Realtime price writing is DISABLED")
    
    print(f"🔍 Monitoring {len(holdings)} holdings...")
    live_prices = fetch_live_prices(holdings)
    
    # Write realtime prices to DB (if enabled)
    if REALTIME_PRICE_ENABLED and live_prices:
        write_realtime_prices_to_db(live_prices)
    
    for h in holdings:
        sid = h["stock_id"]
        
        # Skip holdings with alert disabled
        if h.get("alert_enabled") is False:
            print(f"  ⏭️ {sid}: alerts disabled, skip")
            continue
            
        if sid not in live_prices:
            print(f"  ⚠️ No live price for {sid}")
            continue
            
        current_price = live_prices[sid]
        avg_cost = h["avg_cost"]
        shares = h["shares"]
        
        pct_threshold = float(h.get("pl_pct_thod") or config.get("PL_PERCENT_THRESHOLD") or 5.0)
        amt_threshold = float(h.get("pl_thod") or config.get("PL_THRESHOLD") or 50000)
        
        # P/L Calculation
        pnl = (current_price - avg_cost) * shares
        pnl_pct = (current_price / avg_cost - 1) * 100
        
        # Update holding data
        h["current_price"] = round(current_price, 2)
        
        print(f"  📊 {sid}: Cost={avg_cost:.2f}, Live={current_price:.2f}, P/L={pnl_pct:+.2f}% (${pnl:,.2f})")
        
        # Trigger alert if threshold met
        if abs(pnl_pct) >= pct_threshold or abs(pnl) >= amt_threshold:
            print(f"  🔔 Alert triggered for {sid}!")
            manager.handle_pl_alert({
                "stock_id": sid,
                "stock_name": sid,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "current_price": current_price,
                "avg_cost": avg_cost,
                "shares": shares,
                "threshold_value": pct_threshold if abs(pnl_pct) >= pct_threshold else amt_threshold,
                "threshold_type": "percent" if abs(pnl_pct) >= pct_threshold else "amount",
                "alert_enabled": h.get("alert_enabled", True)
            })

    # Save results back to monitor file
    with open(monitor_path, "w", encoding="utf-8") as f:
        json.dump(holdings, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Updated {monitor_path} with latest P/L values.")

if __name__ == "__main__":
    check_live_alerts()
