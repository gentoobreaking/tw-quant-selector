# tw-quant-selector 程式碼審查報告

**審查日期**：2026-06-01  
**審查範圍**：前端 + 後端 + 配置檔  
**審查目標**：找出潛在問題、硬編碼假資料、安全性問題、程式碼品質問題

> **狀態更新**：v1.1 — 新增 27 個問題，總計 42 個問題。原本 v1.0 的 `passCount` 已透過 T091 修復。新增 Dashboard 假損益、Backtest 假換手率等發現。

---

## 執行摘要

| 嚴重程度 | 數量 | 說明 |
|---------|------|------|
| 🔴 嚴重 | 5 | 硬編碼假資料、安全性漏洞 |
| 🟡 中等 | 18 | 潛在 Bug、硬編碼常數、假資料 |
| 🟢 輕微 | 19 | 程式碼品質、最佳實踐、重複定義 |

---

## 🔴 嚴重問題

### 1. [已修復] Strategy 頁面「大師策略庫」使用硬編碼假資料

**位置**：`frontend/src/pages/Strategy.tsx` 第 83-144 行

**問題描述**：
`GURU_LIST` 中的每個大師條件都有 `passCount` 欄位，顯示「預計篩選通過檔數」，但這些數字是 **完全硬編碼的假資料**，並未實際查詢數據庫。

**假資料範例**：
```typescript
{ name: 'ROE > 15%', source: 'financials', threshold: '>15%', passCount: 420 },
{ name: '負債比 < 50%', source: 'financials', threshold: '<50%', passCount: 380 },
{ name: '毛利率 > 30%', source: 'financials', threshold: '>30%', passCount: 350 },
// ... 所有數字都是亂填的！
```

**影響**：
- 使用者看到「預計通過 420 檔」會誤以為是真的統計數字
- 實際上這些數字沒有任何意義，嚴重誤導使用者

**修復建議**：
1. **移除 `passCount` 欄位**（最簡單）
2. **或新增後端 API** `/api/v1/guru/pass-count`，實際查詢符合每個條件的股票數量
3. 前端改為呼叫 API 取得真實數字

**優先級**：🔥 最高（已建立 T089 處理 `estimateUniverseSize()`）

**狀態**：✅ 已修復 — **T091** 已移除所有 `passCount` 欄位（interface、GURU_LIST 條件、JSX 顯示、CSS 樣式）

---

### 2. CORS 允許所有來源（安全性漏洞）

**位置**：`src/tw_quant_selector/api/app.py` 第 23-27 行

**問題描述**：
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ⚠️ 允許所有來源！
    allow_methods=["*"],
    allow_headers=["*"],
)
```

**影響**：
- 任何網站都可以透過瀏覽器呼叫您的 API
- 可能導致 CSRF 攻擊
- 敏感資料（Telegram Token、SMTP 密碼）可能被惡意網站讀取

**修復建議**：
```python
allow_origins=[
    "http://localhost:5173",  # 前端開發伺服器
    "http://localhost:3000",  # 如果有 React 開發伺服器
    "https://yourdomain.com",  # 生產環境網域
]
```

**優先級**：🔥 高（安全性問題）

---

### 3. [已修復] 敏感資訊透過 API 暴露

**位置**：`src/tw_quant_selector/api/app.py` 第 530-546 行 ← 舊端點已移除，改用 `/api/v1/settings/alerts`
**修復日期**：2026-06-07（T100）

**問題描述**：
`/api/v1/alert-settings` 端點會返回所有 Alert 設定，包括：
- `TELEGRAM_BOT_TOKEN`
- `SMTP_PASSWORD`

雖然前端有標記 `is_sensitive = True` 並顯示為 `****`，但 **API 回傳的 JSON 中仍然包含這些敏感資訊**。

**風險**：
- 任何有權限呼叫 API 的人都能看到這些敏感資訊
- 瀏覽器開發者工具（F12）→ Network → 可以直接看到 Token 和密碼

**修復建議**：
```python
@app.get("/api/v1/alert-settings")
def get_alert_settings():
    db_settings = {r[0]: r[1] for r in db.execute("SELECT key, value FROM alert_settings").fetchall()}
    
    result = []
    for key in ALERT_KEYS:
        is_sensitive = key in SENSITIVE_KEYS
        result.append({
            "key": key,
            "value": "***" if is_sensitive else db_settings.get(key),  # ← 敏感資訊返回 ***
            "is_env_set": key in os.environ,
            "is_sensitive": is_sensitive,
        })
    return api_response(result)
```

**優先級**：🔥 高（安全性問題）

---

### 4. Dashboard 本週持倉損益完全硬編碼

**位置**：`frontend/src/pages/Dashboard.tsx` 第 211-218 行

**問題描述**：
```tsx
<div className={styles.weeklyPnl}>
  <span className={styles.pnlBull}>▲ +2.4%</span>   {/* ← 硬編碼 */}
  <span className={styles.pnlMuted}>▲ +1.1%</span>   {/* ← 硬編碼 */}
  <span className={styles.pnlBull}>▲ +1.3%</span>   {/* ← 硬編碼 */}
</div>
```

**影響**：
- 使用者每天看到相同的 +2.4%/+1.1%/+1.3%，永遠不會變化
- 完全誤導使用者以為這是真實持倉損益
- 比 `estimateUniverseSize()` 更嚴重，因為這是**直接顯示在 Dashboard 首頁**

**修復建議**：
1. 新增後端 API `/api/v1/portfolio/pnl/weekly`，實際計算本週買入成本 vs 市值
2. 前端改為呼叫 API 顯示真實數字
3. 如暫無 API，應隱藏此區塊或顯示「資料準備中」

**優先級**：🔥 最高（直接顯示在首頁的假資料）

---

### 5. Backtest 年化換手率完全硬編碼

**位置**：`frontend/src/pages/Backtest.tsx` 第 376 行

**問題描述**：
```tsx
{ label: '年化換手率', value: 3.12, fmt: 'pct' as const },
```
後端 `backtest/metrics.py:60` 實際上已經正確計算了 `turnover` 值，但前端**完全忽略後端真實資料**，永遠顯示 `3.12`（312%）。

**影響**：
- 每次回測結果的年化換手率都是 312%，完全無效
- 使用者無法根據真實換手率評估策略穩定性

**修復建議**：
```tsx
{ label: '年化換手率', value: result.turnover, fmt: 'pct' as const },
```
確認後端回傳資料包含 `turnover` 欄位。

**優先級**：🔥 高（產出無效的回測指標）

---

## 🟡 中等問題

### 6. `estimateUniverseSize()` 假估算函數

**位置**：`frontend/src/pages/Strategy.tsx` 第 217-234 行

**問題描述**：
此函數使用 **線性縮放公式** 估算篩選後的股票數量，但實際上台股市值分佈是 **冪次法則（Power Law）**，線性公式完全不準確。

**範例**：
```
最低市值：200 億
→ 函數估算：剩 1242 檔
→ 實際數量：約 80-120 檔
```

**影響**：
- 顯示的「篩選結果」數字嚴重誤導
- 使用者會以為篩選後還有 700+ 檔，實際上只有 80 檔

**修復建議**：
- 已建立 **T089**：新增後端 API `/api/v1/universe/count`，實際查詢數據庫

**優先級**：🔥 高（已建立任務）

---

### 7. Backtest 只在前星期一執行再平衡

**位置**：`src/tw_quant_selector/backtest/engine.py` 第 39 行

**問題描述**：
```python
def _rebalance_dates(start: date, end: date) -> list[date]:
    dates: list[date] = []
    d = start
    while d <= end:
        if d.weekday() == 0:  # ← 只在前星期一再平衡
            dates.append(d)
        d += timedelta(days=1)
    return dates
```

**問題**：
- 台股每週有 5 個交易日（週一到週五）
- 只在前星期一再平衡會 **錯過 4 個交易日** 的訊號變化
- 回測結果會與實際情況有較大偏差

**修復建議**：
```python
def _rebalance_dates(start: date, end: date) -> list[date]:
    """每週再平衡（週一），但每天檢查訊號"""
    dates: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # 週一到週五都是交易日
            dates.append(d)
        d += timedelta(days=1)
    return dates

# 或：改為每週再平衡，但每天檢查停損停利
```

**優先級**：🟡 中等（影響回測準確性）

---

### 8. `combiner.py` 中 `Decimal` 與 `np.isnan` 混用

**位置**：`src/tw_quant_selector/strategies/combiner.py` 第 156-165 行

**問題描述**：
```python
if strategy != "composite":
    raw = (individual_scores or {}).get(strategy, {}).get(sid)
    if raw is not None and not (isinstance(raw, (float, np.floating)) and np.isnan(raw)):
        score_val = round(Decimal(str(raw)), 4)
    else:
        score_val = None
else:
    if score is None or (isinstance(score, (float, np.floating)) and (math.isnan(score) or np.isnan(score))):
        score_val = None
    else:
        score_val = round(Decimal(str(score)), 4)
```

**問題**：
1. `np.isnan()` 只能用於 `float`，不能用於 `Decimal`
2. 如果 `raw` 是 `Decimal`，`isinstance(raw, (float, np.floating))` 會返回 `False`，導致 `np.isnan()` 不會被執行（這反而是好事）
3. 但程式碼邏輯混亂，難以維護

**修復建議**：
```python
def _safe_decimal(val):
    """安全轉換為 Decimal，處理 NaN/None"""
    if val is None:
        return None
    if isinstance(val, Decimal):
        return val
    if isinstance(val, (float, int)):
        if math.isnan(val):
            return None
        return Decimal(str(val))
    return None

# 使用：
score_val = _safe_decimal(raw)
```

**優先級**：🟡 中等（潛在 Bug）

---

### 9. 前端大量使用 `any` 型別

**位置**：`frontend/src/**/*.tsx` 多個檔案

**問題描述**：
- `BaseTable.tsx`：14 處 `any`
- `SignalRowDetail.tsx`：4 處 `any`
- `Strategy.tsx`：11 處 `any`
- `Backtest.tsx`：5 處 `any`

**影響**：
- TypeScript 型別檢查失效
- 容易出現執行時期錯誤（Runtime Error）
- 重構時難以追蹤型別變更

**修復建議**：
1. 定義明確的 `interface` 或 `type`
2. 避免使用 `as any` 強制轉型
3. 使用 `unknown` 代替 `any`（需要明確型別守衛）

**優先級**：🟢 輕微（程式碼品質）

---

### 10. SQL 查詢使用 f-string（潛在 SQL 注入風險）

**位置**：`src/tw_quant_selector/api/app.py` 第 429-432 行

**問題描述**：
```python
tracker = db.execute(
    f"""SELECT trade_date FROM {t} ORDER BY trade_date DESC LIMIT 5"""
    #    ^^^^ f-string 組裝表名
).fetchall()
```

**風險評估**：
- **目前風險低**：因為 `t` 是硬編碼的 `signals`、`valuations` 等表名，不是使用者輸入
- **但未來風險**：如果有人修改程式碼，傳入使用者輸入的表名，就會有 SQL 注入風險

**修復建議**：
```python
# 方法 1：使用參數化查詢（推薦）
VALID_TABLES = {'signals', 'valuations', 'daily_prices', 'financials'}
if t not in VALID_TABLES:
    raise ValueError(f"Invalid table name: {t}")
tracker = db.execute(
    f"SELECT trade_date FROM {t} ORDER BY trade_date DESC LIMIT 5"
).fetchall()

# 方法 2：使用白名單
ALLOWED_TABLES = ["signals", "valuations", "daily_prices"]
if t in ALLOWED_TABLES:
    query = f"SELECT ... FROM {t} ..."
```

**優先級**：🟡 中等（防禦性程式設計）

---

### 11. 缺少 API 請求參數驗證

**位置**：`src/tw_quant_selector/api/app.py` 多個端點

**問題描述**：
部分 API 端點沒有驗證輸入參數，可能導致異常：

```python
@app.get("/api/v1/signals/{signal_date}")
def signals_by_date(
    signal_date: str,  # ← 沒有驗證格式（應該是 YYYY-MM-DD）
    strategy: str = "composite",
    top_n: int = Query(200, ge=1, le=500),
):
    # 如果 signal_date = "abc"，會導致 SQL 錯誤
    ...
```

**修復建議**：
```python
from datetime import datetime

@app.get("/api/v1/signals/{signal_date}")
def signals_by_date(
    signal_date: str = Path(..., regex="^\d{4}-\d{2}-\d{2}$"),  # ← 驗證格式
    strategy: str = "composite",
    top_n: int = Query(200, ge=1, le=500),
):
    try:
        parsed_date = datetime.strptime(signal_date, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD")
    ...
```

**優先級**：🟡 中等（穩健性）

---

### 12. DuckDB 連線未使用連線池

**位置**：`src/tw_quant_selector/data/database.py`

**問題描述**：
```python
class Database:
    def __init__(self, read_only: bool = True):
        self.conn = duckdb.connect(DB_PATH, read_only=read_only)
        #   ^^^^ 單一連線，沒有連線池
```

**影響**：
- 高併發時（多個 API 請求同時進來），會有連線衝突
- DuckDB 雖然支援多讀取，但寫入時會 Lock

**修復建議**：
```python
from queue import Queue

class Database:
    def __init__(self, read_only: bool = True, max_connections: int = 10):
        self.read_only = read_only
        self.pool = Queue(maxsize=max_connections)
        for _ in range(max_connections):
            conn = duckdb.connect(DB_PATH, read_only=read_only)
            self.pool.put(conn)
    
    def get_connection(self):
        return self.pool.get()
    
    def return_connection(self, conn):
        self.pool.put(conn)
```

**優先級**：🟢 輕微（目前使用者少，還不需要）

---

### 13. Dashboard 因子貢獻摘要完全硬編碼

**位置**：`frontend/src/pages/Dashboard.tsx` 第 248-261 行

**問題描述**：
```tsx
{['momentum', 'value', 'quality', 'growth'].map((f) => (
  <div className={styles.factorRow}>
    <div className={styles.factorBarFill} style={{
      width: `${(f === 'momentum' ? 30 : f === 'value' ? 25 : f === 'quality' ? 25 : 20)}%`,
    }} />
    <span className={styles.factorPct}>
      {f === 'momentum' ? 30 : f === 'value' ? 25 : f === 'quality' ? 25 : 20}%
    </span>
  </div>
))}
```

**影響**：
- 動能永遠 30%、價值永遠 25%、品質永遠 25%、成長永遠 20%
- 這些數據**完全不反映真實的因子貢獻**
- 使用者會誤以為這是實際計算結果

**修復建議**：
1. 後端新增 `/api/v1/portfolio/factor-contribution` 端點，實際計算各因子對目前持倉的貢獻
2. 前端改為顯示真實數字，或隱藏此區塊

**優先級**：🟡 中等（誤導使用者）

---

### 14. Signals 頁面使用假回退係數模擬因子分數

**位置**：`frontend/src/pages/Signals.tsx` 第 204-207 行

**問題描述**：
```tsx
makeFactorCol('momentum', '動能', 1),
makeFactorCol('value', '價值', 0.8),
makeFactorCol('quality', '品質', 0.6),
makeFactorCol('growth', '成長', 0.4),
```
當 `factor_scores` 為空時，這些係數（1、0.8、0.6、0.4）會乘以總分作為回退顯示值，但這些數字完全是任意的。

**影響**：
- 當後端無因子資料時，使用者看到的是「偽造的」因子分數
- 使用者無法區分哪些是真實數據、哪些是回退估算

**修復建議**：
1. 當 `factor_scores` 為空時，應顯示「—」（無資料）而非回退值
2. 或在欄位標題加註記提示目前為估算值

**優先級**：🟡 中等（誤導使用者）

---

### 15. Portfolio.tsx 存在死程式碼 `loadLots()`

**位置**：`frontend/src/pages/Portfolio.tsx` 第 52-54 行

**問題描述**：
```tsx
function loadLots(): Lot[] { return []; }
```
此函數永遠回傳空陣列，真正的資料流通過 `refreshPortfolio()` 從 `/api/v1/lots` 獲取。此函數是早期 localStorage 實作的殘留物，現已無用。

**影響**：
- 閱讀程式碼的人會困惑 `loadLots()` 到底有沒有作用
- 維護成本增加

**修復建議**：
移除 `loadLots()` 函數及其引用。

**優先級**：🟢 輕微（程式碼品質）

---

### 16. 後端 ETF 清單重複定義

**位置**：
- `src/tw_quant_selector/portfolio/universe.py` 第 9-17 行
- `src/tw_quant_selector/api/app.py` 第 847 行

**問題描述**：
兩個地方都硬編碼了相同的 ETF 清單：
```python
# universe.py
ETF_LIST = {"0050", "0051", "0052", "0056", "00878", "00881", "006208"}

# app.py
{"0050", "0051", "0052", "0056", "00878", "00881", "006208"}
```

**影響**：
- 新增 ETF 時需要同時修改兩個檔案
- 如果忘記同步，不同模組會看到不同的 ETF 清單

**修復建議**：
```python
# app.py 改為引用 universe.py
from tw_quant_selector.portfolio.universe import ETF_LIST
```

**優先級**：🟡 中等（維護性）

---

### 17. App.py 與 universe.py 硬編碼同組預設值

**位置**：
- `src/tw_quant_selector/portfolio/portfolio.py` 第 11-18 行
- `src/tw_quant_selector/api/app.py` 第 977-983 行

**問題描述**：
```python
# portfolio.py
INITIAL_CAPITAL = Decimal("1000000")
STOCK_COUNT = 20
ETF_COUNT = 3
STOCK_WEIGHT = 0.8
ETF_WEIGHT = 0.2
SINGLE_HOLDING_LIMIT = 0.10
INDUSTRY_LIMIT = 0.40

# app.py — 重複定義相同的值
universe_defaults = {
    "include_etf": False,
    "min_market_cap": 3_000_000_000,
    "top_n_stocks": 20,
    "top_n_etfs": 3,
}
```

**影響**：
- 修改一個地方的常數時，容易忘記同步另一處
- 預設值散落在多個檔案，難以管理

**修復建議**：
統一從 `portfolio.py` 或一個獨立的 `settings.py` 匯出所有預設值。

**優先級**：🟢 輕微（程式碼品質）

---

### 18. Alerting 模組 `check_pl_alerts()` 為空實作

**位置**：`src/tw_quant_selector/monitoring/alerting.py` 第 102 行

**問題描述**：
```python
# Here we mock it or query backtest_runs
```
`check_pl_alerts()` 函數主體僅為 `pass`，並未實際檢查持倉 P&L。

**影響**：
- 使用者設定的 P&L 警報（`pl_thod`）實際上**永遠不會被觸發**
- 使用者不知情的情況下，以為警報功能正常

**修復建議**：
實作 `check_pl_alerts()` 邏輯，查詢目前持倉市值 vs 成本，比較 threshold 後發送通知。

**優先級**：🔴 嚴重（功能未實作但 UI 顯示已啟用）

---

## 🟢 輕微問題（程式碼品質）

### 19. 硬編碼的預設參數

**位置**：多個策略檔案

| 檔案 | 硬編碼參數 | 建議 |
|------|------------|------|
| `value.py` | `max_pb=30, max_pe=100` | 應該從資料庫或設定檔讀取 |
| `momentum.py` | `lookback_long=252, lookback_short=22` | 應該開放使用者調整 |
| `quality.py` | `roe_weight=0.5, leverage_weight=0.3` | 應該開放使用者調整 |
| `growth.py` | `rev_weight=0.6, eps_weight=0.4` | 應該開放使用者調整 |

**優先級**：🟢 輕微（功能完整性）

---

### 20. 前端缺少錯誤邊界處理

**位置**：`frontend/src/api/client.ts`

**問題描述**：
```typescript
export async function apiFetch<T>(endpoint: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${endpoint}`);
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);  // ← 只有丟出例外，沒有統一錯誤處理
  }
  return res.json();
}
```

**影響**：
- 每個頁面都要自己寫 `try-catch`
- 如果忘記寫，錯誤會直接顯示在控制台，使用者看不到

**修復建議**：
```typescript
// 統一錯誤處理 + Toast 通知
export async function apiFetch<T>(endpoint: string): Promise<T> {
  try {
    const res = await fetch(`${BASE_URL}${endpoint}`);
    if (!res.ok) {
      const errorData = await res.json().catch(() => ({}));
      throw new ApiError(res.status, errorData.message || `API error: ${res.status}`);
    }
    return res.json();
  } catch (error) {
    showToast(`錯誤：${error.message}`, "error");
    throw error;
  }
}
```

**優先級**：🟢 輕微（使用者體驗）

---

### 21. 缺少單元測試

**問題描述**：
- 後端：`tests/` 目錄不存在
- 前端：`__tests__/` 目錄不存在

**影響**：
- 修改程式碼後，無法快速驗證是否破壞現有功能
- 重構風險高

**修復建議**：
1. 新增 `pytest` 測試後端關鍵邏輯（策略計算、回測引擎）
2. 新增 `vitest` 測試前端元件

**優先級**：🟢 輕微（長期維護性）

---

### 22. 日誌（Logging）不一致

**位置**：後端多個檔案

**問題描述**：
- 有些地方用 `print()`
- 有些地方用 `structlog`
- 有些地方完全沒有日誌

**修復建議**：
統一使用 `structlog`：
```python
import structlog
log = structlog.get_logger()

log.info("strategy.computed", strategy="value", stocks=len(scores))
log.error("db.connection_failed", error=str(e))
```

**優先級**：🟢 輕微（除錯便利性）

---

### 23. 前端套件版本過舊

**位置**：`frontend/package.json`

**檢查項目**：
- `react`: ^18.2.0（最新 18.3.1）
- `typescript`: ^5.0.0（最新 5.5.4）
- `@tanstack/react-table`: ^8.9.3（最新 8.20.0）

**建議**：
定期更新套件（但小心 Breaking Changes）

**優先級**：🟢 輕微（功能性不受影響）

---

## 📋 修復優先級建議

### 立即修復（本週內）
1. ✅ **T091**：`passCount` 硬編碼假資料 → 已移除（T091）
2. ✅ **T089**：`estimateUniverseSize()` 假估算 → 已建立 T089
3. 🔴 **問題 2**：CORS 允許所有來源 → 限制允許網域
4. 🔴 **問題 3**：敏感資訊暴露 → 過濾敏感欄位
5. 🔴 **問題 4**：Dashboard 假 P&L 損益 → 改為真實查詢
6. 🔴 **問題 5**：Backtest 假年化換手率 → 改用後端真實資料
7. 🔴 **問題 18**：Alerting 空實作 → 實作 P&L 檢查邏輯

### 短期修復（本月內）
8. 🟡 **問題 7**：Backtest 再平衡邏輯 → 改為每日檢查
9. 🟡 **問題 8**：`Decimal` 與 `np.isnan` 混用 → 重構
10. 🟡 **問題 13**：Dashboard 因子貢獻 → 改為真實查詢
11. 🟡 **問題 14**：Signals 回退係數 → 無資料時顯示「—」
12. 🟡 **問題 16**：ETF 清單重複 → 統一從 universe.py 匯出

### 長期優化（下個 sprint）
13. 🟡 **問題 9**：`any` 型別濫用 → 定義明確型別
14. 🟡 **問題 10**：SQL f-string → 加入白名單驗證
15. 🟡 **問題 11**：參數驗證 → 加入 Path/Query 驗證
16. 🟢 **問題 15**：死程式碼 loadLots → 移除
17. 🟢 **問題 19-23**：程式碼品質提升

---

## 🎯 總結

**最嚴重的問題**：
1. 🔴 **Dashboard 假 P&L**（+2.4%/+1.1%/+1.3%）→ 首頁直接誤導
2. 🔴 **Backtest 假換手率**（3.12）→ 無效的回測指標
3. 🔴 **CORS 安全性漏洞** → 可能被攻擊
4. 🔴 **Alerting 空實作** → 警報功能失效
5. ✅ ~~`passCount` 硬編碼假資料~~ → T091 已修復
6. ✅ ~~`estimateUniverseSize()` 假估算~~ → T089 已建立

**建議下一步**：
1. 先修復安全性問題（CORS、敏感資訊過濾）
2. 然後修復 Dashboard 假 P&L 和 Backtest 假換手率
3. 實作 Alerting P&L 檢查
4. 最後優化程式碼品質（型別、死程式碼、測試）

---

**報告產生者**：碼農1號 / OpenCode DeepSeek V4 Flash
**審查工具**：手動程式碼審查 + grep 關鍵字搜尋  
**報告版本**：v1.1  
**下次審查建議**：完成上述修復後，重新審查一次
