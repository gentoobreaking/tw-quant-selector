# Institutional TopN API + 前端排行表

## 后端新增

### `GET /api/v1/institutional/top`
- **参数**: `top_n`(default 10), `sort_by`(total_net/foreign_investors_net/sity_investors_net/dealer_net), `order`(desc/asc), `date`(可选, 默认最新交易日)
- **安全**: ORDER BY 使用 CASE WHEN + 参数化，不拼接用户输入到 SQL 标识符
- **文件**: `src/tw_quant_selector/api/app.py` — 新增 ~50 行

## 前端新增

### API Client
- **fetchInstitutionalTop()**: `frontend/src/api/client.ts`
- **类型**: InstTopItem, InstTopResult

### InstitutionalFlow 页面
- 新增 **買賣超排行** 区块（图表下方）
- 4 个维度按钮（合計/外資/投信/自營商），点击切换买超↔卖超
- 🟢买超 / 🔴卖超 标识
- Top 10 表格：排名、股票、三大法人净额（红色买入/绿色卖出）、收盘价
- **CSS**: `.topSection`, `.topControls`, `.topBtn`, `.topTable` 等

## 验证
- `tsc -b`: 0 errors
- `npx vitest run`: 33/33 passed
- Python ast: Syntax OK
