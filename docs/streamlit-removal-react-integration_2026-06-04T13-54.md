# Streamlit 移除 + React 整合增强

## 决策
用户选择 **方案 C**：砍掉独立 Streamlit 容器，把 T118/T119 分析看板功能整合到已有 React 页面。

## 原因
- Streamlit 镜像 1.94GB，依赖 51 个包（altair/pydeck/watchdog 等），与 FastAPI 依赖树无关
- Streamlit 6 个页面中，5 个在 React 侧已有对应页面，高度冗余
- 唯一缺失的是「即時行情」页面，已补到 Dashboard

## 清理内容
| 清理项 | 状态 |
|--------|------|
| `streamlit/` 目录（8 文件） | ✅ 已删除 |
| `Dockerfile.streamlit` | ✅ 已删除 |
| `requirements_streamlit.txt` | ✅ 已删除 |
| `docker-compose.yml` streamlit 服务 | ✅ 已移除 |
| `tw-quant-streamlit:latest` 镜像 | ✅ 已删除 |
| Sidebar 外部链接入口 | ✅ 已移除 |
| Sidebar.module.css externalLinks 样式 | ✅ 已移除 |

## React 增强

### 1. Dashboard — 即時行情整合
- **修复 Bug**：`liveQuotes` state 未定义 — WebSocket 数据一直被丢弃，现已修复
- **新增 PE 列**：表格新增即时 PE/PB 列，使用 `RealtimeValuationBadge` 组件渲染
- **新增市场概况**：涨跌家数统计 + 平均 PE，4 卡片网格
- **WebSocket 数据扩展**：从只抓 `{price, change_pct}` 改为追 `{price, change_pct, pe_realtime, pb_realtime, volume}`
- Dashboard.module.css 新增 `.marketOverview`、`.marketGrid` 样式

### 2. 已有页面覆盖对照
| Streamlit 页面 | React 路由 | 已有功能 |
|---------------|-----------|---------|
| 今日選股 | `/signals` | 日期筛选、策略选择、FactorMiniBar、排行表、汇出 CSV |
| 法人動向 | `/institutional-flow` | KPI 卡片（外/投/自）、个股搜寻、ComposedChart |
| 即時行情 | `/` (Dashboard) | WebSocket 即时报价、PE/PB 列、涨跌统计 ✅ 新增强 |
| 回測工作台 | `/backtest` | equity curves、参数输入、lightweight-charts、CSV 下载 |
| 因子研究 | `/factor-research` | 4 页签：IC/分层报酬/相关性矩阵/法人验证 |
| 警示歷史 | `/alert-history` | 筛选器、BarChart、警示列表、解决按钮 |

## 验证
- TypeScript 编译：0 errors
- 前端测试：33/33 passed
- `docker compose config`：无 streamlit 服务残留

## 关键文件变更
- `frontend/src/pages/Dashboard.tsx` — 新增 liveQuotes state、PE 列、市场概况
- `frontend/src/pages/Dashboard.module.css` — 新增 marketOverview/marketGrid 样式
- `frontend/src/components/Sidebar.tsx` — 移除 streamlitUrl 外部链接
- `frontend/src/components/Sidebar.module.css` — 移除 externalLinks 样式
- `docker-compose.yml` — 移除 streamlit 服务
