-- 001-init-schema.sql
-- T102: Schema 迁移（DuckDB → PostgreSQL）
-- 创建所有表格、索引、主键、外键

-- 清理（若重复执行）
DROP TABLE IF EXISTS backtest_equity CASCADE;
DROP TABLE IF EXISTS backtest_positions CASCADE;
DROP TABLE IF EXISTS backtest_runs CASCADE;
DROP TABLE IF EXISTS alert_log CASCADE;
DROP TABLE IF EXISTS alert_settings CASCADE;
DROP TABLE IF EXISTS lots CASCADE;
DROP TABLE IF EXISTS operation_logs CASCADE;
DROP TABLE IF EXISTS strategy_config_history CASCADE;
DROP TABLE IF EXISTS guru_scores CASCADE;
DROP TABLE IF EXISTS ingestion_tracker CASCADE;
DROP TABLE IF EXISTS signals CASCADE;
DROP TABLE IF EXISTS valuations CASCADE;
DROP TABLE IF EXISTS financials CASCADE;
DROP TABLE IF EXISTS monthly_revenue CASCADE;
DROP TABLE IF EXISTS daily_prices CASCADE;
DROP TABLE IF EXISTS portfolio CASCADE;
DROP TABLE IF EXISTS stocks CASCADE;

-- 删除序列（若重复执行）
DROP SEQUENCE IF EXISTS seq_operation_logs_id CASCADE;
DROP SEQUENCE IF EXISTS seq_strategy_config_history_id CASCADE;

-- ============================================================
-- 1. stocks（股票基本资料）
-- ============================================================
CREATE TABLE stocks (
    stock_id     VARCHAR(10)   NOT NULL,
    stock_name   VARCHAR(50)   NOT NULL,
    market       VARCHAR(10)   NOT NULL,
    industry     VARCHAR(50),
    list_date    DATE,
    delist_date  DATE,
    is_etf       BOOLEAN       DEFAULT FALSE,
    created_at   TIMESTAMP      DEFAULT NOW(),
    PRIMARY KEY (stock_id)
);

CREATE INDEX idx_stocks_market    ON stocks(market);
CREATE INDEX idx_stocks_industry  ON stocks(industry);

COMMENT ON TABLE  stocks IS '股票基本资料';
COMMENT ON COLUMN stocks.is_etf IS '是否为 ETF';

-- ============================================================
-- 2. daily_prices（每日行情）
-- ============================================================
CREATE TABLE daily_prices (
    stock_id   VARCHAR(10)   NOT NULL,
    trade_date DATE           NOT NULL,
    open       DECIMAL(10,2),
    high       DECIMAL(10,2),
    low        DECIMAL(10,2),
    close      DECIMAL(10,2),
    volume     BIGINT,
    amount     DECIMAL(18,2),
    adj_factor DECIMAL(10,6),
    adj_close  DECIMAL(10,4),
    PRIMARY KEY (stock_id, trade_date),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_daily_prices_date    ON daily_prices(trade_date);
CREATE INDEX idx_daily_prices_stock  ON daily_prices(stock_id);

COMMENT ON TABLE  daily_prices IS '每日行情（已除权息还原）';

-- ============================================================
-- 3. monthly_revenue（月营收）
-- ============================================================
CREATE TABLE monthly_revenue (
    stock_id        VARCHAR(10)   NOT NULL,
    year_month      VARCHAR(7)    NOT NULL,  -- 格式：2026-05
    revenue         BIGINT,
    revenue_yoy     DECIMAL(12,4),
    announcement_date DATE,
    PRIMARY KEY (stock_id, year_month),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_monthly_revenue_date ON monthly_revenue(year_month);

COMMENT ON TABLE  monthly_revenue IS '月营收（公开资讯观测站）';

-- ============================================================
-- 4. financials（财务报表）
-- ============================================================
CREATE TABLE financials (
    stock_id            VARCHAR(10)   NOT NULL,
    year_quarter        VARCHAR(7)    NOT NULL,  -- 格式：2026Q1
    revenue             BIGINT,
    gross_profit        BIGINT,
    operating_income    BIGINT,
    net_income          BIGINT,
    eps                 DECIMAL(8,2),
    roe                 DECIMAL(8,4),
    roa                 DECIMAL(8,4),
    gross_margin        DECIMAL(8,4),
    operating_margin    DECIMAL(8,4),
    debt_to_equity      DECIMAL(8,4),
    total_assets        BIGINT,
    total_liabilities   BIGINT,
    cash                BIGINT,
    current_assets      BIGINT,
    current_liabilities BIGINT,
    net_fixed_assets    BIGINT,
    ebit                BIGINT,
    enterprise_value     BIGINT,
    roic                DECIMAL(8,4),
    peg                 DECIMAL(8,4),
    current_ratio       DECIMAL(8,4),
    announcement_date   DATE,
    PRIMARY KEY (stock_id, year_quarter),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_financials_quarter ON financials(year_quarter);

COMMENT ON TABLE  financials IS '财务报表（季别）';

-- ============================================================
-- 5. valuations（评价指标）
-- ============================================================
CREATE TABLE valuations (
    stock_id   VARCHAR(10)   NOT NULL,
    trade_date DATE           NOT NULL,
    pe_ratio   DECIMAL(10,2),
    pb_ratio   DECIMAL(10,2),
    dividend_yield DECIMAL(8,4),
    market_cap DECIMAL(18,2),
    PRIMARY KEY (stock_id, trade_date),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_valuations_date ON valuations(trade_date);

COMMENT ON TABLE  valuations IS '评价指针（本益比、净市盈率、殖利率、市值）';

-- ============================================================
-- 6. signals（选股信号）
-- ============================================================
CREATE TABLE signals (
    signal_date DATE        NOT NULL,
    stock_id   VARCHAR(10) NOT NULL,
    strategy   VARCHAR(50) NOT NULL,
    score      DECIMAL(8,4),
    rank       INTEGER,
    is_selected BOOLEAN,
    PRIMARY KEY (signal_date, stock_id, strategy),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_signals_date     ON signals(signal_date);
CREATE INDEX idx_signals_strategy ON signals(strategy);

COMMENT ON TABLE  signals IS '选股信号（每日收盘後产生）';

-- ============================================================
-- 7. backtest_runs（回测运行纪录）
-- ============================================================
CREATE TABLE backtest_runs (
    run_id        VARCHAR(64)   NOT NULL,
    run_at        TIMESTAMP,
    start_date    DATE,
    end_date      DATE,
    strategy_config JSONB,
    total_return  DECIMAL(8,4),
    cagr          DECIMAL(8,4),
    sharpe        DECIMAL(8,4),
    max_drawdown  DECIMAL(8,4),
    calmar        DECIMAL(8,4),
    turnover      DECIMAL(8,4),
    result_path   VARCHAR(255),
    PRIMARY KEY (run_id)
);

COMMENT ON TABLE  backtest_runs IS '回测运行纪录';

-- ============================================================
-- 8. backtest_positions（回测持仓明细）
-- ============================================================
CREATE TABLE backtest_positions (
    run_id     VARCHAR(64)   NOT NULL,
    trade_date DATE           NOT NULL,
    stock_id   VARCHAR(10)   NOT NULL,
    action     VARCHAR(10)   NOT NULL,  -- 'BUY' / 'SELL' / 'HOLD'
    shares     INTEGER,
    price      DECIMAL(10,2),
    value      DECIMAL(18,2),
    weight     DECIMAL(8,4),
    PRIMARY KEY (run_id, trade_date, stock_id),
    FOREIGN KEY (run_id)   REFERENCES backtest_runs(run_id)   ON DELETE CASCADE,
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_backtest_positions_run ON backtest_positions(run_id);

COMMENT ON TABLE  backtest_positions IS '回测持仓明细';

-- ============================================================
-- 9. backtest_equity（回测净值曲线）
-- ============================================================
CREATE TABLE backtest_equity (
    run_id          VARCHAR(64)   NOT NULL,
    trade_date      DATE           NOT NULL,
    portfolio_value DECIMAL(18,2),
    benchmark_value DECIMAL(18,2),
    drawdown        DECIMAL(8,4),
    PRIMARY KEY (run_id, trade_date),
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id) ON DELETE CASCADE
);

CREATE INDEX idx_backtest_equity_run ON backtest_equity(run_id);

COMMENT ON TABLE  backtest_equity IS '回测净值曲线';

-- ============================================================
-- 10. portfolio（即时持仓）
-- ============================================================
CREATE TABLE portfolio (
    stock_id     VARCHAR(10)   NOT NULL,
    avg_cost     DECIMAL(18,2) NOT NULL,
    shares       INTEGER        NOT NULL,
    is_etf       BOOLEAN       DEFAULT FALSE,
    updated_at   TIMESTAMP      DEFAULT NOW(),
    pl_thod      DOUBLE PRECISION,
    pl_pct_thod  DOUBLE PRECISION,
    alert_enabled BOOLEAN       DEFAULT TRUE,
    PRIMARY KEY (stock_id),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

COMMENT ON TABLE  portfolio IS '即时持仓（人工或 API 同步）';

-- ============================================================
-- 11. lots（持仓明细（分批买进））
-- ============================================================
CREATE SEQUENCE seq_lots_id;

CREATE TABLE lots (
    id         VARCHAR(64)   DEFAULT ('lot_' || nextval('seq_lots_id')) NOT NULL,
    stock_id   VARCHAR(10)   NOT NULL,
    date       DATE           NOT NULL,
    shares     INTEGER        NOT NULL,
    cost       DECIMAL(18,2) NOT NULL,
    created_at TIMESTAMP      DEFAULT NOW(),
    PRIMARY KEY (id),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_lots_stock ON lots(stock_id);

COMMENT ON TABLE  lots IS '持仓明细（分批买进，用于加权平均成本计算）';

-- ============================================================
-- 12. alert_settings（警示设定）
-- ============================================================
CREATE TABLE alert_settings (
    key          VARCHAR(64)   NOT NULL,
    value        VARCHAR(255),
    is_sensitive BOOLEAN       DEFAULT FALSE,
    updated_at   TIMESTAMP      DEFAULT NOW(),
    PRIMARY KEY (key)
);

COMMENT ON TABLE  alert_settings IS '警示设定（阈值、开关等）';

-- ============================================================
-- 13. alert_log（警示发送纪录）
-- ============================================================
CREATE TABLE alert_log (
    log_id          VARCHAR(64)   NOT NULL,
    stock_id        VARCHAR(10),
    triggered_at    TIMESTAMP      DEFAULT NOW(),
    pnl             DECIMAL(18,2),
    pnl_pct         DECIMAL(8,4),
    threshold_type   VARCHAR(20),
    threshold_value  DECIMAL(18,2),
    avg_cost        DECIMAL(18,2),
    current_price   DECIMAL(10,2),
    shares          INTEGER,
    sent            BOOLEAN,
    reason          VARCHAR(255),
    PRIMARY KEY (log_id),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE SET NULL
);

CREATE INDEX idx_alert_log_stock    ON alert_log(stock_id);
CREATE INDEX idx_alert_log_triggered ON alert_log(triggered_at);

COMMENT ON TABLE  alert_log IS '警示发送纪录';

-- ============================================================
-- 14. operation_logs（操作日志）
-- ============================================================
CREATE SEQUENCE seq_operation_logs_id;

CREATE TABLE operation_logs (
    id          VARCHAR(64) DEFAULT ('log_' || nextval('seq_operation_logs_id')) NOT NULL,
    module      VARCHAR(50) NOT NULL,
    event       VARCHAR(100) NOT NULL,
    severity    VARCHAR(20) NOT NULL,  -- 'INFO' / 'WARN' / 'ERROR' / 'CRITICAL'
    created_at  TIMESTAMP    DEFAULT NOW(),
    PRIMARY KEY (id)
);

CREATE INDEX idx_operation_logs_module ON operation_logs(module);
CREATE INDEX idx_operation_logs_created ON operation_logs(created_at);

COMMENT ON TABLE  operation_logs IS '操作日志（系统事件追踪）';

-- ============================================================
-- 15. strategy_config_history（策略设定历史）
-- ============================================================
CREATE SEQUENCE seq_strategy_config_history_id;

CREATE TABLE strategy_config_history (
    config_id        INTEGER   NOT NULL DEFAULT nextval('seq_strategy_config_history_id'),
    changed_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    weights          JSONB,
    advanced_params  JSONB,
    guru_config      JSONB,
    universe_config  JSONB,
    changed_by      VARCHAR(50) DEFAULT 'user',
    note            VARCHAR(255),
    PRIMARY KEY (config_id)
);

CREATE INDEX idx_strategy_config_history_changed ON strategy_config_history(changed_at);

COMMENT ON TABLE  strategy_config_history IS '策略设定历史版本纪录';

-- ============================================================
-- 16. guru_scores（大师策略评分）
-- ============================================================
CREATE TABLE guru_scores (
    score_date      DATE         NOT NULL,
    stock_id        VARCHAR(10) NOT NULL,
    guru            VARCHAR(50) NOT NULL,  -- 'piotroski' / 'graham' / etc.
    score           DECIMAL(8,4),
    pass_filter     BOOLEAN,
    criteria_detail JSONB,
    PRIMARY KEY (score_date, stock_id, guru),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_guru_scores_guru  ON guru_scores(guru);
CREATE INDEX idx_guru_scores_date  ON guru_scores(score_date);

COMMENT ON TABLE  guru_scores IS '大师策略评分（Piotroski F-Score 等）';

-- ============================================================
-- 17. ingestion_tracker（资料摄取进度追踪）
-- ============================================================
CREATE TABLE ingestion_tracker (
    stock_id     VARCHAR(10)   NOT NULL,
    dataset      VARCHAR(50)   NOT NULL,  -- 'daily_prices' / 'financials' / etc.
    bucket       INTEGER,
    last_updated DATE,
    last_status   VARCHAR(20),  -- 'success' / 'error' / 'skip'
    error_msg     VARCHAR(500),
    PRIMARY KEY (stock_id, dataset),
    FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX idx_ingestion_tracker_dataset ON ingestion_tracker(dataset);
CREATE INDEX idx_ingestion_tracker_updated ON ingestion_tracker(last_updated);

COMMENT ON TABLE  ingestion_tracker IS '资料摄取进度追踪（支持增量更新）';

-- ============================================================
-- 完成讯息
-- ============================================================
DO $$
BEGIN
    RAISE NOTICE 'Schema 创建完成，共 17 个表格';
END $$;
