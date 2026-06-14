# 即時損益監控使用手冊 (Live P/L Monitoring)

本系統提供基於 **TWSE 即時行情 API** 的損益監控功能，能在盤中自動計算持股報酬率，並於觸發門檻時透過 Telegram 或 Email 發送告警。

---

## 🚀 快速啟動流程

### 方法一：使用 CSV 快速設定 (推薦)

編輯專案根目錄下的 `stock_monitor.csv`，格式如下：

```csv
stock_id, avg_cost, shares, is_etf, pl_pct_thod, pl_thod, alert_enabled
0050, 89.21, 1303, TRUE, 10, 10000, TRUE
```

| 欄位 | 說明 |
|------|------|
| `stock_id` | 股票代碼 |
| `avg_cost` | 平均成本 |
| `shares` | 持股數量 |
| `is_etf` | 是否為 ETF (`TRUE`/`FALSE`) |
| `pl_pct_thod` | 百分比報酬率警報門檻 (%) |
| `pl_thod` | 金額損益警報門檻 (元) |
| `alert_enabled` | 啟用監控 (`TRUE`/`FALSE`，預設 `TRUE`) |

執行同步（寫入 PostgreSQL）：

```bash
cd /Users/claw/Projects/tw-quant-selector
docker compose exec app python3 scripts/sync_portfolio_csv.py
```

啟動即時監控：

```bash
docker compose exec app python3 scripts/check_live_alerts.py
```

> ⚠️ **注意**：資料已從 DuckDB 遷移至 PostgreSQL，所有腳本現在直接操作 PostgreSQL，不再需要 `.duckdb` 檔案。

---

### 方法二：透過資料庫更新 (進階)

監控系統讀取 `portfolio` 表的資料。

1. **寫入持股資料**（PostgreSQL `INSERT ... ON CONFLICT` 語法）：

```bash
docker compose exec app python3 -c "
from tw_quant_selector.data.database import Database
db = Database()
with db.connection() as conn:
    conn.execute('''
        INSERT INTO portfolio (stock_id, avg_cost, shares, is_etf)
        VALUES (:sid, :cost, :shares, :etf)
        ON CONFLICT (stock_id) DO UPDATE SET
            avg_cost = EXCLUDED.avg_cost,
            shares = EXCLUDED.shares,
            is_etf = EXCLUDED.is_etf,
            updated_at = CURRENT_TIMESTAMP
    ''', {'sid': '2330', 'cost': 600.0, 'shares': 1000, 'etf': False})
db.close()
"
```

2. **同步至監控清單**：

```bash
docker compose exec app python3 scripts/export_portfolio.py
```

3. **啟動即時監控**：

```bash
docker compose exec app python3 scripts/check_live_alerts.py
```

---

## ⏰ 排程自動化 (Cron Job)

建議將監控腳本加入系統排程，在開盤期間自動執行。

執行 `crontab -e` 並加入以下設定（每 10 分鐘檢查一次）：

```cron
# 週一至五 09:00 - 13:40 執行
*/10 9-13 * * 1-5 cd /Users/claw/Projects/tw-quant-selector && docker compose exec -T app python3 scripts/check_live_alerts.py >> ~/logs/live_alerts.log 2>&1
```

> Docker 環境下使用 `docker compose exec -T`（無 TTY）來支援 cron 的非互動執行。

---

## ⚙️ 告警設定

### 告警門檻

- **報酬率門檻** (`pl_pct_thod`): 預設 `5.0` (%)
- **損益金額門檻** (`pl_thod`): 預設 `50000` (元)

可在 `stock_monitor.csv` 中針對個別持倉設定，或直接修改 `portfolio` 表的 `pl_pct_thod` / `pl_percent_threshold` 欄位。

### 通知管道

確保 `.env` 中已設定：
- `TELEGRAM_BOT_TOKEN`: 機器人 Token
- `TELEGRAM_CHAT_ID`: 您的 Chat ID
- `SMTP_*`: 若需 Email 通知請設定 SMTP 參數

### 冷卻機制 (Cooldown)

同一檔股票觸發警報後，系統進入 **4 小時冷卻期**。冷卻期內的重複觸發僅記錄於日誌，不會重複發送通知。

---

## 🛠 資料架構變更摘要（DuckDB → PostgreSQL）

| 項目 | 舊（DuckDB） | 新（PostgreSQL） |
|------|-------------|-----------------|
| 主資料庫 | `data/tw_quant.duckdb` | PostgreSQL (`DB_*` env) |
| 即時價格寫入 | DuckDB `ATTACH` + 專用 `.duckdb` 檔案 | PostgreSQL `realtime_prices` 表 |
| CSV 同步語法 | `INSERT OR REPLACE` | `INSERT ... ON CONFLICT DO UPDATE` |
| 占位符 | `?` (DuckDB style) | `:name` (SQLAlchemy named params) |
| 腳本 Database 初始化 | `Database(DB_PATH)` | `Database()` |

---

## 🗂️ 檔案說明

- `src/tw_quant_selector/data/database.py` — PostgreSQL `Database` 類（自動轉換 `?` 占位符，兼容原始 SQL 字串）
- `scripts/sync_portfolio_csv.py` — 將 `stock_monitor.csv` 同步至 PostgreSQL `portfolio` 表
- `scripts/export_portfolio.py` — 將 `portfolio` 表匯出至 `.stock_monitor.json` 供監控器使用
- `scripts/check_live_alerts.py` — 核心監控邏輯，對接 TWSE 即時行情 API，即時價格寫入 `realtime_prices` 表
- `.stock_monitor.json` — 盤中監控目標清單（快取，由 `export_portfolio.py` 產生）
- `src/tw_quant_selector/monitoring/alerting.py` — AlertManager 告警邏輯
