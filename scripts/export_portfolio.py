#!/usr/bin/env python3
import csv
import json
import os
import sys
from pathlib import Path

# Add project root and src to path
root = Path(__file__).parent.parent
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "src"))

from tw_quant_selector.data.database import Database

def export_portfolio():
    db = Database()
    
    rows = db.execute("""
        SELECT p.stock_id, p.avg_cost, p.shares, p.is_etf, s.market,
               p.pl_pct_thod, p.pl_thod, p.alert_enabled
        FROM portfolio p
        LEFT JOIN stocks s ON p.stock_id = s.stock_id
    """).fetchall()
    
    portfolio_data = []
    for r in rows:
        portfolio_data.append({
            "stock_id": r[0],
            "avg_cost": float(r[1]),
            "shares": int(r[2]),
            "is_etf": bool(r[3]),
            "market": (r[4] or "TSE").upper(),
            "pl_pct_thod": float(r[5]) if r[5] is not None else None,
            "pl_thod": float(r[6]) if r[6] is not None else None,
            "alert_enabled": bool(r[7]) if r[7] is not None else True
        })
    
    json_path = root / ".stock_monitor.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(portfolio_data, f, indent=2, ensure_ascii=False)
    
    csv_path = root / "stock_monitor.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["stock_id", "avg_cost", "shares", "is_etf", "pl_pct_thod", "pl_thod", "alert_enabled"])
        for h in portfolio_data:
            writer.writerow([h["stock_id"], h["avg_cost"], h["shares"],
                             "TRUE" if h["is_etf"] else "FALSE",
                             h["pl_pct_thod"] if h["pl_pct_thod"] is not None else "",
                             h["pl_thod"] if h["pl_thod"] is not None else "",
                             "TRUE" if h["alert_enabled"] else "FALSE"])
    
    print(f"✅ Exported {len(portfolio_data)} holdings to {json_path}")
    print(f"✅ Exported {len(portfolio_data)} holdings to {csv_path}")

if __name__ == "__main__":
    export_portfolio()
