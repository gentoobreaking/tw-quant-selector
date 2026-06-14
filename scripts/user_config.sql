-- T107: PostgreSQL 效能调优配置
-- 执行：psql -U tw-quant -d tw_quant -f scripts/user_config.sql

-- 1. 启用 pg_stat_statements（需重启容器）
ALTER SYSTEM SET shared_preload_libraries = 'pg_stat_statements';
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- 2. 连接池建议（在 Python database.py 中设置）
-- pool_size=5, max_overflow=5, pool_recycle=3600

-- 3. 自动真空确认
SHOW autovacuum;

-- 4. 手动真空分析（数据量较大时执行）
-- VACUUM ANALYZE signals;
-- VACUUM ANALYZE daily_prices;
-- VACUUM ANALYZE valuations;

-- 5. 监控慢查询
-- SELECT query, mean_exec_time, calls, rows
-- FROM pg_stat_statements
-- ORDER BY mean_exec_time DESC
-- LIMIT 10;

-- 6. 查看长时间运行的查询
-- SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
-- FROM pg_stat_activity
-- WHERE state != 'idle'
-- ORDER BY duration DESC;
