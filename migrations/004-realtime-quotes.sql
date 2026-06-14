-- 004-realtime-quotes.sql
-- Add realtime_quotes table for live TWSE quote feed.
-- This table was defined in models.py but missing from 001-init-schema.
CREATE TABLE IF NOT EXISTS realtime_quotes (
    stock_id        VARCHAR(10) NOT NULL,
    quote_time      TIMESTAMP NOT NULL,
    price           DECIMAL(10, 2),
    volume          BIGINT,
    bid             DECIMAL(10, 2),
    ask             DECIMAL(10, 2),
    change_amt      DECIMAL(10, 2),
    change_pct      DECIMAL(8, 4),
    is_close        BOOLEAN DEFAULT FALSE,
    pe_realtime     DECIMAL(10, 2),
    pb_realtime     DECIMAL(10, 2),
    yield_realtime  DECIMAL(8, 4),
    PRIMARY KEY (stock_id, quote_time),
    CONSTRAINT fk_rtq_stock FOREIGN KEY (stock_id) REFERENCES stocks(stock_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_realtime_quotes_time ON realtime_quotes (quote_time);
