-- 005-intraday-kline.sql
-- Add open/high/low columns to realtime_quotes for o/h/l data from MIS
ALTER TABLE realtime_quotes ADD COLUMN IF NOT EXISTS open_price DECIMAL(10, 2);
ALTER TABLE realtime_quotes ADD COLUMN IF NOT EXISTS high_price DECIMAL(10, 2);
ALTER TABLE realtime_quotes ADD COLUMN IF NOT EXISTS low_price DECIMAL(10, 2);

-- Create intraday_kline table for aggregated K-line data
CREATE TABLE IF NOT EXISTS intraday_kline (
    stock_id    VARCHAR(10) NOT NULL,
    k_time      TIMESTAMP   NOT NULL,
    period_min  INTEGER     NOT NULL DEFAULT 60,
    open        DECIMAL(10, 2),
    high        DECIMAL(10, 2),
    low         DECIMAL(10, 2),
    close       DECIMAL(10, 2),
    volume      BIGINT,
    PRIMARY KEY (stock_id, k_time, period_min),
    CONSTRAINT fk_ik_stock FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_intraday_kline_time ON intraday_kline (k_time);
CREATE INDEX IF NOT EXISTS ix_intraday_kline_stock_time ON intraday_kline (stock_id, k_time);

-- Add config_json column to alert_rules for storing technical rule parameters
ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS config_json TEXT DEFAULT '{}';
-- Add message_template column to alert_rules for customizable alert messages
ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS message_template TEXT;

-- Add operating_cash_flow column to financials for T129 (CFO from cash flow statements)
ALTER TABLE financials ADD COLUMN IF NOT EXISTS operating_cash_flow bigint;
