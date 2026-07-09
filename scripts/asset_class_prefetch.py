"""
T140 - 台股資產分類與價格預先下載模組

從 PostgreSQL stocks 表讀取全市場股票/ETF 清單，完成三類資產分類，
並透過 yfinance 下載 2021-01-01 ~ 今的含息調整收盤價，輸出中間 pickle 檔。

用法:
    python scripts/asset_class_prefetch.py
"""
import json
import os
import sys
import time
import pickle
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from tw_quant_selector.data.database import Database

DIVIDEND_KEYWORDS = ["高息", "股息", "配息", "永續", "ESG", "公司治理", "綠能", "電信債", "能源", "醫療", "優息"]
BATCH_SIZE = 50
BACKOFF_INITIAL = 10
BACKOFF_MAX = 120
MAX_RETRIES = 5

OUTPUT_DIR = Path(__file__).parent.parent / "output" / "asset_comparison_2021_2026"
START_DATE = "2021-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")


TRADITIONAL_ETF_PREFIXES = ("00", "01")


def classify_etf(name: str) -> str:
    name_upper = name.upper()
    for kw in DIVIDEND_KEYWORDS:
        if kw in name_upper or kw in name:
            return "配息型ETF"
    return "市场型ETF"


def is_likely_etf(stock_id: str) -> bool:
    return stock_id.startswith(TRADITIONAL_ETF_PREFIXES)


HOT_STOCKS_LIST = ["2330", "2317", "2454", "2412", "2308", "2881", "2882", "2002"]
# 0050 is already captured via is_likely_etf logic below


def get_top50_stocks(db) -> list[dict]:
    today_str = datetime.now().strftime("%Y-%m-%d")
    rows = db.execute("""
        SELECT dp.stock_id, s.stock_name, s.market, dp.amount
        FROM daily_prices dp
        JOIN stocks s ON s.stock_id = dp.stock_id
        WHERE s.is_etf = false
          AND s.delist_date IS NULL
          AND NOT (s.stock_id LIKE '00%' OR s.stock_id LIKE '01%')
          AND dp.trade_date = (
              SELECT MAX(trade_date) FROM daily_prices
          )
        ORDER BY dp.amount DESC
        LIMIT 50
    """).fetchall()
    result = {r[0]: {"id": r[0], "name": r[1], "market": r[2]} for r in rows}
    existing_ids = set(result.keys())
    for sid in HOT_STOCKS_LIST:
        if sid not in existing_ids:
            extra = db.execute(
                "SELECT stock_name, market FROM stocks WHERE stock_id = :sid AND is_etf = false AND delist_date IS NULL",
                {"sid": sid},
            ).fetchone()
            if extra:
                result[sid] = {"id": sid, "name": extra[0], "market": extra[1]}
                print(f"    + 加入熱門股 {sid} {extra[0]}")
    return list(result.values())


def get_etfs(db) -> tuple[list[dict], list[dict]]:
    market_etfs = []
    dividend_etfs = []
    rows = db.execute("""
        SELECT stock_id, stock_name, market
        FROM stocks
        WHERE (is_etf = true OR stock_id LIKE '00%' OR stock_id LIKE '01%')
          AND delist_date IS NULL
        ORDER BY stock_id
    """).fetchall()
    for r in rows:
        sid, name, market = r[0], r[1], r[2]
        cat = classify_etf(name)
        entry = {"id": sid, "name": name, "market": market}
        if cat == "配息型ETF":
            dividend_etfs.append(entry)
        else:
            market_etfs.append(entry)
    return market_etfs, dividend_etfs


def _fetch_batch(tickers: list[str]) -> pd.DataFrame | None:
    backoff = BACKOFF_INITIAL
    for attempt in range(MAX_RETRIES):
        try:
            data = yf.download(
                tickers, start=START_DATE, end=END_DATE,
                group_by="column", threads=False, progress=False,
                auto_adjust=True,
            )
            if data is None or data.empty:
                return None
            if isinstance(data.columns, pd.MultiIndex):
                level0 = data.columns.get_level_values(0).unique()
                if "Close" in level0:
                    return data["Close"]
                if "Adj Close" in level0:
                    return data["Adj Close"]
                first_price = data.xs(data.columns[0][0], axis=1, level=0)
                first_price.columns = [c[1] for c in data.columns if c[0] == data.columns[0][0]]
                return first_price
            if data.shape[1] >= 1:
                return data
            return None
        except YFRateLimitError:
            print(f"  Rate limited, waiting {backoff}s...")
            time.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        except Exception as e:
            print(f"  Batch error: {e}")
            return None
    return None


def download_prices(assets: list[dict], category: str) -> pd.DataFrame:
    tickers = []
    ticker_map = {}
    for a in assets:
        sid = a["id"]
        market = a.get("market", "TSE")
        suffix = ".TWO" if market == "OTC" else ".TW"
        ticker = f"{sid}{suffix}"
        tickers.append(ticker)
        ticker_map[ticker] = sid

    all_prices = []
    failed = []
    total = len(tickers)
    for i in range(0, total, BATCH_SIZE):
        batch = tickers[i:i+BATCH_SIZE]
        print(f"  [{category}] Batch {i//BATCH_SIZE+1}/{(total+BATCH_SIZE-1)//BATCH_SIZE}: {batch[0]}...{batch[-1]} ({len(batch)} tickers)")
        adj = _fetch_batch(batch)
        if adj is not None and not adj.empty:
            all_prices.append(adj)
        else:
            for t in batch:
                failed.append(ticker_map.get(t, t))
        if i + BATCH_SIZE < total:
            time.sleep(2)

    if not all_prices:
        print(f"  WARNING: No data downloaded for {category}")
        return pd.DataFrame()

    combined = pd.concat(all_prices, axis=1)
    combined = combined.loc[:, ~combined.columns.duplicated()]
    combined.columns = [ticker_map.get(c, c) for c in combined.columns]
    combined.index = pd.to_datetime(combined.index)
    combined = combined.sort_index()
    combined = combined.ffill().bfill()

    valid_count = combined.dropna(how="all").shape[1]
    print(f"  Downloaded {valid_count}/{total} assets for {category}")
    if failed:
        print(f"  Failed: {failed}")

    return combined


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    db = Database()

    print("=== T140: 資產分類與價格預先下載 ===")
    print(f"期間: {START_DATE} ~ {END_DATE}")
    print()

    print("1. 取得前 50 大權值股...")
    top50 = get_top50_stocks(db)
    print(f"   取得 {len(top50)} 檔: {[a['id'] for a in top50[:5]]}...")

    print("2. 取得 ETF 清單並分類...")
    market_etfs, dividend_etfs = get_etfs(db)
    print(f"   市場型 ETF: {len(market_etfs)} 檔")
    print(f"   配息型 ETF: {len(dividend_etfs)} 檔")

    classified = {
        "台股": top50,
        "市场型ETF": market_etfs,
        "配息型ETF": dividend_etfs,
    }
    with open(OUTPUT_DIR / "assets_classified.json", "w", encoding="utf-8") as f:
        json.dump(classified, f, ensure_ascii=False, indent=2)
    print("   已寫入 assets_classified.json")

    print()
    print("3. 下載含息調整價格...")
    metadata = {}
    for cat_name, cat_assets in classified.items():
        print(f"\n--- {cat_name} ({len(cat_assets)} 檔) ---")
        prices = download_prices(cat_assets, cat_name)
        if not prices.empty:
            pkl_path = OUTPUT_DIR / f"prices_{cat_name}.pkl"
            with open(pkl_path, "wb") as f:
                pickle.dump(prices, f)
            metadata[cat_name] = {
                "count": len(cat_assets),
                "downloaded": int(prices.dropna(how="all").shape[1]),
                "date_range": [
                    str(prices.index[0].date()) if not prices.empty else None,
                    str(prices.index[-1].date()) if not prices.empty else None,
                ],
                "rows": len(prices),
            }
            print(f"   -> prices_{cat_name}.pkl ({prices.shape})")
        else:
            metadata[cat_name] = {"count": len(cat_assets), "downloaded": 0, "error": "no data"}

    with open(OUTPUT_DIR / "prices_metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    print("\n=== T140 完成 ===")
    for cat, m in metadata.items():
        print(f"  {cat}: {m.get('downloaded', 0)}/{m['count']} 檔, {m.get('rows', 0)} 筆")


if __name__ == "__main__":
    main()
