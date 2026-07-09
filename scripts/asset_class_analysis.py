"""
T141 - 指標計算引擎

讀取 T140 產出的資產清單與含息價格，計算每檔資產的績效指標。

用法:
    python scripts/asset_class_analysis.py
"""
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

RISK_FREE_RATE = 0.015
OUTPUT_DIR = Path(__file__).parent.parent / "output" / "asset_comparison_2021_2026"


def calc_metrics(prices: pd.Series, name: str, stock_id: str, initial: float = 10000.0) -> dict:
    prices = prices.dropna()
    if len(prices) < 100:
        return {"stock_id": stock_id, "name": name, "error": f"insufficient data ({len(prices)} days)"}

    first_price = prices.iloc[0]
    last_price = prices.iloc[-1]
    if first_price <= 0:
        return {"stock_id": stock_id, "name": name, "error": "first price <= 0"}

    years = (prices.index[-1] - prices.index[0]).days / 365.25
    final_value = initial * last_price / first_price
    total_return = final_value / initial - 1
    cagr = (final_value / initial) ** (1 / years) - 1 if years > 0 else total_return

    daily_returns = prices.pct_change().dropna().values
    if len(daily_returns) < 2:
        return {"stock_id": stock_id, "name": name, "error": "not enough returns"}

    ann_return = float(np.mean(daily_returns)) * 252
    ann_vol = float(np.std(daily_returns, ddof=1)) * np.sqrt(252)
    sharpe = (ann_return - RISK_FREE_RATE) / ann_vol if ann_vol > 0 else 0

    cummax = np.maximum.accumulate(prices.values)
    dd = (prices.values - cummax) / cummax
    max_dd = float(np.min(dd))
    dd_start_idx = int(np.argmax(np.maximum.accumulate(prices.values)))
    dd_end_idx = int(np.argmin(dd))

    weekly = prices.resample("W-FRI").last().pct_change().dropna()
    monthly = prices.resample("ME").last().pct_change().dropna()
    quarterly = prices.resample("QE").last().pct_change().dropna()

    weekly_mean = float(weekly.mean()) if len(weekly) > 0 else 0
    monthly_mean = float(monthly.mean()) if len(monthly) > 0 else 0
    quarterly_mean = float(quarterly.mean()) if len(quarterly) > 0 else 0
    weekly_std = float(weekly.std()) if len(weekly) > 1 else 0
    monthly_std = float(monthly.std()) if len(monthly) > 1 else 0
    quarterly_std = float(quarterly.std()) if len(quarterly) > 1 else 0

    equity_curve = prices / first_price * initial

    year_returns = {}
    for y in [2021, 2022, 2023, 2024, 2025, 2026]:
        y_prices = prices[prices.index.year == y]
        if len(y_prices) >= 50:
            y_ret = y_prices.iloc[-1] / y_prices.iloc[0] - 1
            year_returns[f"return_{y}"] = round(float(y_ret), 4)

    result = {
        "stock_id": stock_id,
        "name": name,
        "initial_value": initial,
        "final_value": round(float(final_value), 2),
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "ann_return": round(ann_return, 4),
        "ann_volatility": round(ann_vol, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "dd_start_date": str(prices.index[dd_start_idx].date()) if dd_start_idx < len(prices) else "",
        "dd_end_date": str(prices.index[dd_end_idx].date()) if dd_end_idx < len(prices) else "",
        "weekly_avg_return": round(weekly_mean, 6),
        "monthly_avg_return": round(monthly_mean, 6),
        "quarterly_avg_return": round(quarterly_mean, 6),
        "weekly_volatility": round(weekly_std, 6),
        "monthly_volatility": round(monthly_std, 6),
        "quarterly_volatility": round(quarterly_std, 6),
        "n_days": len(prices),
        "years": round(years, 2),
    }
    result.update(year_returns)
    return result


def main():
    print("=== T141: 指標計算引擎 ===")

    with open(OUTPUT_DIR / "assets_classified.json", "r", encoding="utf-8") as f:
        classified = json.load(f)

    results = {}
    equity_curves = {}
    for cat_name, cat_assets in classified.items():
        pkl_path = OUTPUT_DIR / f"prices_{cat_name}.pkl"
        if not pkl_path.exists():
            print(f"  SKIP {cat_name}: prices file not found")
            continue

        print(f"\n--- {cat_name} ({len(cat_assets)} 檔) ---")
        with open(pkl_path, "rb") as f:
            prices_df: pd.DataFrame = pickle.load(f)

        cat_results = []
        cat_equity = {}
        for a in cat_assets:
            sid = a["id"]
            name = a["name"]
            if sid not in prices_df.columns:
                continue
            series = prices_df[sid]
            m = calc_metrics(series, name, sid)
            if "error" in m:
                print(f"  SKIP {sid} {name}: {m['error']}")
                continue
            cat_results.append(m)
            cat_equity[sid] = {"name": name, "series": series / series.iloc[0] * 10000}

        cat_results.sort(key=lambda x: x["total_return"], reverse=True)
        results[cat_name] = cat_results

        equity_df = pd.DataFrame({
            sid: eq["series"] for sid, eq in cat_equity.items()
        })
        equity_curves[cat_name] = equity_df

        print(f"  成功: {len(cat_results)}/{len(cat_assets)} 檔")

    with open(OUTPUT_DIR / "metrics_all.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_DIR / "equity_curves.pkl", "wb") as f:
        pickle.dump(equity_curves, f)

    print("\n=== T141 完成 ===")
    for cat, r in results.items():
        if r:
            top = r[0]
            print(f"  {cat} TOP1: {top['stock_id']} {top['name']} 報酬率={top['total_return']*100:.1f}% CAGR={top['cagr']*100:.1f}%")
            print(f"  共 {len(r)} 檔")


if __name__ == "__main__":
    main()
