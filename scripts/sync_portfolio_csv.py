#!/usr/bin/env python3
import csv
import json
import sys
import os
from pathlib import Path

# Add src to path to import Database
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tw_quant_selector.data.database import Database

def convert_csv_to_json(csv_path: str, json_path: str):
    holdings = []
    db = Database(read_only=False)
    
    try:
        with open(csv_path, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f, skipinitialspace=True)
            for row in reader:
                pl_pct_raw = row.get("pl_pct_thod", "").strip()
                pl_raw = row.get("pl_thod", "").strip()
                alert_raw = row.get("alert_enabled", "").strip()
                holding = {
                    "stock_id": row["stock_id"].strip(),
                    "avg_cost": float(row["avg_cost"].strip()),
                    "shares": int(row["shares"].strip()),
                    "is_etf": row["is_etf"].strip().upper() == "TRUE",
                    "pl_pct_thod": float(pl_pct_raw) if pl_pct_raw else None,
                    "pl_thod": float(pl_raw) if pl_raw else None,
                    "alert_enabled": alert_raw.upper() != "FALSE" if alert_raw else True,
                    "market": "TSE" 
                }
                holdings.append(holding)
        
        # 1. Update JSON
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(holdings, f, indent=2, ensure_ascii=False)
            
        # 2. Update Database
        with db.connection() as conn:
            for h in holdings:
                conn.execute("""
                    INSERT INTO portfolio (stock_id, avg_cost, shares, is_etf, pl_pct_thod, pl_thod, alert_enabled)
                    VALUES (:stock_id, :avg_cost, :shares, :is_etf, :pl_pct_thod, :pl_thod, :alert_enabled)
                    ON CONFLICT (stock_id) DO UPDATE SET
                        avg_cost = EXCLUDED.avg_cost,
                        shares = EXCLUDED.shares,
                        is_etf = EXCLUDED.is_etf,
                        pl_pct_thod = EXCLUDED.pl_pct_thod,
                        pl_thod = EXCLUDED.pl_thod,
                        alert_enabled = EXCLUDED.alert_enabled,
                        updated_at = CURRENT_TIMESTAMP
                """, h)
                # Also create a lot entry so portfolio can be managed via lots
                lid = f"csv_{h['stock_id']}_{int(Path(csv_path).stat().st_mtime)}"
                # Remove existing CSV-generated lot for this stock to avoid duplicates
                conn.execute("DELETE FROM lots WHERE stock_id = :sid AND id LIKE 'csv_%'", {"sid": h["stock_id"]})
                if h["shares"] > 0:
                    conn.execute("""
                        INSERT INTO lots (id, stock_id, date, shares, cost)
                        VALUES (:id, :stock_id, CURRENT_DATE, :shares, :cost)
                    """, {"id": lid, "stock_id": h["stock_id"], "shares": h["shares"], "cost": h["avg_cost"]})
        
        print(f"✅ Successfully converted {csv_path} to {json_path} and updated DB")
        print(f"   Processed {len(holdings)} holdings.")
        
    except Exception as e:
        print(f"❌ Error converting CSV: {e}")
        sys.exit(1)

if __name__ == "__main__":
    base_dir = Path(__file__).parent.parent
    csv_file = base_dir / "stock_monitor.csv"
    json_file = base_dir / ".stock_monitor.json"
    
    convert_csv_to_json(str(csv_file), str(json_file))
