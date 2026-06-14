# 后端运行时错误修复

## 错误 1: Health Check SQL – boolean = integer
**文件**: `src/tw_quant_selector/monitoring/alerting.py:473`
**错误**: `s.is_selected = 1` 在 PostgreSQL 中 boolean 列不能直接与 int 比较
**修复**: 改为 `s.is_selected IS TRUE`

## 错误 2: monthly_revenue FK 违规
**文件**: `src/tw_quant_selector/data/ingestion.py:229-239`
**错误**: TWSE 返回的 stock_id=1435 不在 `stocks` 表中，_upsert 写入 monthly_revenue 时触发外键约束
**修复**: 在 `update_monthly_revenue_from_twse` 中写入前查询 stocks 表，过滤掉不存在的 stock_id，记录 skip 数量
- 这比让写入失败更优雅 — TWSE 数据包含新上市或退市股票，不应阻断整个批次
- stock 再次被加入 stocks 表后，下次调度会自动补齐 revenue 数据

## 错误 3: TPEX 机构法人 302 跳转 (仅记录)
**文件**: `src/tw_quant_selector/data/twstock_client.py`
**现象**: TPEX openapi/v1/tpex_3d_investorsInfo_daily 返回 302 跳转到首页
**状态**: 已有 try/catch + warning 日志，无需修改
**原因**: 今日(2026-06-04)数据尚未发布（盘前或无交易），TPEX 服务器返回 redirect 作为空数据的 fallback 行为
