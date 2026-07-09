# 台股資產類別比較分析 — 任務規劃 (T140 ~ T142)

---

## T140 - 資產分類與價格預先下載

**目標**: 建立 `scripts/asset_class_prefetch.py`，從 PostgreSQL 讀取 stocks 表，完成三類資產分類，透過 yfinance 下載 5 年含息調整價格，輸出中間檔案。

**分類邏輯**:
- **台股前50權值股**: `is_etf=false`，依最新日 `adj_close × volume` 排序取前50
- **市場型ETF**: `is_etf=true` 且名稱不含配息關鍵字
- **配息型ETF**: `is_etf=true` 且名稱含 `高息/股息/配息/永續/ESG/公司治理/綠能/金融/電信債/能源/醫療`

**下載**: yfinance `Adj Close`，2021-01-01 ~ 今，每批50檔，指數backoff重試。

**輸出**:
- `output/asset_comparison_2021_2026/assets_classified.json` — 分類清單
- `output/asset_comparison_2021_2026/prices_{category}.pkl` — 各類含息價格 DataFrame
- `output/asset_comparison_2021_2026/prices_metadata.json` — 下載狀態

**驗收**: DB連線正常、分類正確(0050→市場, 0056→配息)、前50包含2330、pkl檔案產出

---

## T141 — 指標計算引擎

**檔案**: `scripts/asset_class_analysis.py`

**輸入**: T140 產出的 `assets_classified.json` + `prices_*.pkl`

**計算指標** (每檔資產, 基準 $10,000):

| 指標 | 公式 |
|------|------|
| 最終淨值 | $10,000 × 最後AdjClose / 最初AdjClose |
| 總報酬率 | (最終淨值 / $10,000) - 1 |
| CAGR(年化) | (最終淨值/$10,000)^(1/年數) - 1 |
| 年化波動度 | 日報酬率 std × √252 |
| Sharpe Ratio | (年化報酬 - 1.5%Rf) / 年化波動度 |
| 最大回撤(MDD) | min((淨值-高峰)/高峰) |
| 每週平均漲跌率 | 週報酬率 mean |
| 每月平均漲跌率 | 月報酬率 mean |
| 每季平均漲跌率 | 季報酬率 mean |
| 每週波動度 | 週報酬率 std |
| 每月波動度 | 月報酬率 std |
| 每季波動度 | 季報酬率 std |

**排序**: 每類別按總報酬率降冪

**輸出**:
- `output/asset_comparison_2021_2026/metrics_{category}.json` — 完整指標
- `output/asset_comparison_2021_2026/equity_curves_{category}.pkl` — 每日淨值曲線

**驗收**: 台積電5年報酬率合理、0050 vs 0056報酬有差異、Sharpe/MDD/波動度數值合理

---

## T142 — 輸出表格與圖表 + 驗證

**檔案**: `scripts/asset_class_report.py`（主入口腳本）

**表格產出** (markdown格式):

1. `comparison_table_台股.md` — 前50權值股排序表
2. `comparison_table_市场型ETF.md` — 市場型ETF排序表
3. `comparison_table_配息型ETF.md` — 配息型ETF排序表
4. `summary_comparison.md` — 三類彙總對照（平均總報酬、平均MDD、平均Sharpe、平均波動度）

**表格欄位**: `代碼 | 名稱 | 總報酬率 | CAGR | 年化波動度 | Sharpe | 最大回撤 | 每週均漲跌 | 每月均漲跌 | 每季均漲跌 | 每週波動度 | 每月波動度 | 每季波動度`

**圖表** (`nav_growth_chart_5y.png`):
- matplotlib 繪製
- X軸: 日期(2021~2026)
- Y軸: 淨值($10,000起算)
- 線: 三類中位數 + 指定熱門股(2330,2317,2454,2412,2308,2881,2882,2002)
- 三類用不同色系、熱門股用灰色虛線
- 中文字型設定(Noto Sans CJK / STHeiti)
- 含標題、圖例、網格

**主入口腳本功能**:
1. 檢查 T140 中間檔案是否存在，若無則自動執行 T140
2. 執行 T141 指標計算
3. 執行 T142 表格+圖表輸出
4. 結果摘要

**驗收**: 表格格式正確、圖表中文字型正常、熱門股標記明顯、三類ETF區別可辨識、執行時間 < 5分鐘(已下載過後)

---

## 相依關係
```
T140 ──→ T141 ──→ T142
```
三個腳本可獨立執行也串接執行。

## 執行方式
```bash
# 全部執行
python scripts/asset_class_report.py

# 或單步
python scripts/asset_class_prefetch.py
python scripts/asset_class_analysis.py
python scripts/asset_class_report.py
```