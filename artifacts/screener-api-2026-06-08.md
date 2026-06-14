# Screener API 上線記錄

## 新增檔案

### `src/tw_quant_selector/api/screener.py`

全新的後端模組，統一彙整 screener 需要的各項指標：

- **PE / PB / Dividend Yield** — 從 `valuations` 表取最新交易日資料
- **EPS (TTM)** — 從 `financials` 表取最近 4 季 EPS 加總（SUM of latest 4 quarters）
- **ROE** — 從 `financials` 表取最新季度 ROE（乘 100 轉為百分比）
- **CAGR (1年報酬)** — 從 `stock_cagr_cache` 表 JOIN（由既有的 CAGR 管線維護）
- **股名 / 產業 / 市場** — 從 `stocks` 表 JOIN

**端點**：`GET /api/v1/screener`

回應格式：
```json
{
  "data": {
    "stocks": {
      "2330": { "price": 2365, "cagr_1y": 141.24, "pe": 31.8,
                "pb": 10.41, "dy": 0.93, "eps": 74.39, "roe": 9.72,
                "fill_days": null, "name": "台積電", "industry": "",
                "market": "twse" },
      ...
    },
    "count": 1930
  },
  "meta": { "cached_at": "...", "date": "2026-06-05" }
}
```

## 修改的檔案

### `app.py`
- 註冊 `/api/v1/screener` 端點
- 在 lifespan 啟動時 warm cache

### `twse-screener.html`（前端）
- **移除** BWIBBU API fetch（`TWSE_BWIBBU`）
- **移除** CAGR API fetch（`CAGR_API` + `cagrMap`）
- **新增** 單一 `SCREENER_API` fetch 替換上述兩支 API
- EPS / ROE 改由後端 DB 提供（非前端推導）
- `dividendYield` 改從 screener API 的 `dy` 欄位（DB `valuations.dividend_yield`）

## 資料覆蓋率

| 欄位 | 有資料 | 佔比 | 資料源 |
|------|--------|------|--------|
| PE | 831 | 43% | valuations |
| Dividend Yield | 876 | 45% | valuations |
| EPS (TTM) | 350 | 18% | financials (FinMind) |
| ROE | 322 | 17% | financials (FinMind) |
| CAGR | 1820 | 94% | stock_cagr_cache (yfinance) |
| 填息天數 | 0 | 0% | 暫無資料源 |

**備註**：
- EPS/ROE 覆蓋率偏低（350 檔）是因為 FinMind 只收錄約 350 檔個股的財報資料。
  - 覆蓋到的股票，資料比舊版更高品質（實際 TTM EPS vs 從 PE 反推的約略值）
  - 舊版 EPS/ROE 實質上只對有 BWIBBU PE 的股票有效（約 1078 檔），但原本因 Content-Type 問題全部空白
- 填息天數尚無資料，需要新增 dividend 資料表與計算排程
