from __future__ import annotations
"""
T104: SQLAlchemy ORM Models
对应 migrations/001-init-schema.sql 的 17 张表
"""
from sqlalchemy import (
    Column, String, Date, DateTime, Boolean, Numeric, Integer,
    Text, PrimaryKeyConstraint, Index, ForeignKey, Sequence,
    DECIMAL, BIGINT, TIMESTAMP, MetaData,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base(metadata=MetaData(schema="selector"))
Base.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Stock(Base):
    __tablename__ = "stocks"

    stock_id    = Column(String(10), primary_key=True)
    stock_name  = Column(String(50), nullable=False)
    market      = Column(String(10), nullable=False, index=True)
    industry    = Column(String(50), index=True)
    list_date   = Column(Date)
    delist_date = Column(Date)
    is_etf      = Column(Boolean, default=False)
    created_at  = Column(TIMESTAMP, default=datetime.now)

    # Relations
    daily_prices       = relationship("DailyPrice", back_populates="stock", cascade="all, delete-orphan")
    monthly_revenue    = relationship("MonthlyRevenue", back_populates="stock", cascade="all, delete-orphan")
    financials         = relationship("Financial", back_populates="stock", cascade="all, delete-orphan")
    valuations         = relationship("Valuation", back_populates="stock", cascade="all, delete-orphan")
    signals            = relationship("Signal", back_populates="stock", cascade="all, delete-orphan")
    guru_scores        = relationship("GuruScore", back_populates="stock", cascade="all, delete-orphan")
    ingestion_tracker  = relationship("IngestionTracker", back_populates="stock", cascade="all, delete-orphan")
    portfolio          = relationship("Portfolio", back_populates="stock", cascade="all, delete-orphan")
    lots               = relationship("Lot", back_populates="stock", cascade="all, delete-orphan")
    alert_log              = relationship("AlertLog", back_populates="stock")
    alert_history          = relationship("AlertHistory", back_populates="stock")
    institutional_flows    = relationship("InstitutionalFlow", back_populates="stock", cascade="all, delete-orphan")
    institutional_holdings = relationship("InstitutionalHolding", back_populates="stock", cascade="all, delete-orphan")
    realtime_quotes        = relationship("RealtimeQuoteModel", back_populates="stock", cascade="all, delete-orphan")
    intraday_snapshots     = relationship("IntradaySnapshot", back_populates="stock", cascade="all, delete-orphan")
    intraday_kline         = relationship("IntradayKline", back_populates="stock", cascade="all, delete-orphan")


class DailyPrice(Base):
    __tablename__ = "daily_prices"

    stock_id   = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    open       = Column(DECIMAL(10, 2))
    high       = Column(DECIMAL(10, 2))
    low        = Column(DECIMAL(10, 2))
    close      = Column(DECIMAL(10, 2))
    volume     = Column(BIGINT)
    amount     = Column(DECIMAL(18, 2))
    adj_factor = Column(DECIMAL(10, 6))
    adj_close  = Column(DECIMAL(10, 4))

    __table_args__ = (
        Index("ix_daily_prices_date", "trade_date"),
        Index("ix_daily_prices_stock", "stock_id"),
    )

    stock = relationship("Stock", back_populates="daily_prices")


class MonthlyRevenue(Base):
    __tablename__ = "monthly_revenue"

    stock_id         = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    year_month       = Column(String(7), primary_key=True)  # '2026-05'
    revenue          = Column(BIGINT)
    revenue_yoy      = Column(DECIMAL(12, 4))
    announcement_date = Column(Date)

    __table_args__ = (
        Index("ix_monthly_revenue_date", "year_month"),
    )

    stock = relationship("Stock", back_populates="monthly_revenue")


class Financial(Base):
    __tablename__ = "financials"

    stock_id             = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    year_quarter         = Column(String(7), primary_key=True)  # '2026Q1'
    revenue              = Column(BIGINT)
    gross_profit         = Column(BIGINT)
    operating_income     = Column(BIGINT)
    net_income           = Column(BIGINT)
    eps                  = Column(DECIMAL(8, 2))
    roe                  = Column(DECIMAL(8, 4))
    roa                  = Column(DECIMAL(8, 4))
    gross_margin         = Column(DECIMAL(8, 4))
    operating_margin     = Column(DECIMAL(8, 4))
    debt_to_equity       = Column(DECIMAL(8, 4))
    total_assets         = Column(BIGINT)
    total_liabilities    = Column(BIGINT)
    cash                 = Column(BIGINT)
    current_assets       = Column(BIGINT)
    current_liabilities  = Column(BIGINT)
    net_fixed_assets     = Column(BIGINT)
    ebit                 = Column(BIGINT)
    enterprise_value      = Column(BIGINT)
    roic                 = Column(DECIMAL(8, 4))
    peg                  = Column(DECIMAL(8, 4))
    current_ratio        = Column(DECIMAL(8, 4))
    announcement_date     = Column(Date)

    __table_args__ = (
        Index("ix_financials_quarter", "year_quarter"),
    )

    stock = relationship("Stock", back_populates="financials")


class Valuation(Base):
    __tablename__ = "valuations"

    stock_id     = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    trade_date   = Column(Date, primary_key=True)
    pe_ratio     = Column(DECIMAL(10, 2))
    pb_ratio     = Column(DECIMAL(10, 2))
    dividend_yield = Column(DECIMAL(8, 4))
    market_cap   = Column(DECIMAL(18, 2))

    __table_args__ = (
        Index("ix_valuations_date", "trade_date"),
    )

    stock = relationship("Stock", back_populates="valuations")


class Signal(Base):
    __tablename__ = "signals"

    signal_date = Column(Date, primary_key=True)
    stock_id    = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    strategy    = Column(String(50), primary_key=True)
    score       = Column(DECIMAL(8, 4))
    rank        = Column(Integer)
    is_selected = Column(Boolean)

    __table_args__ = (
        Index("ix_signals_date", "signal_date"),
        Index("ix_signals_strategy", "strategy"),
    )

    stock = relationship("Stock", back_populates="signals")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    run_id         = Column(String(64), primary_key=True)
    run_at         = Column(TIMESTAMP)
    start_date     = Column(Date)
    end_date       = Column(Date)
    strategy_config = Column(JSONB)
    total_return   = Column(DECIMAL(8, 4))
    cagr           = Column(DECIMAL(8, 4))
    sharpe         = Column(DECIMAL(8, 4))
    max_drawdown   = Column(DECIMAL(8, 4))
    calmar         = Column(DECIMAL(8, 4))
    turnover       = Column(DECIMAL(8, 4))
    benchmark      = Column(String(10), default="0050")
    result_path    = Column(String(255))

    positions = relationship("BacktestPosition", back_populates="run", cascade="all, delete-orphan")
    equity    = relationship("BacktestEquity", back_populates="run", cascade="all, delete-orphan")


class BacktestPosition(Base):
    __tablename__ = "backtest_positions"

    run_id     = Column(String(64), ForeignKey("backtest_runs.run_id", ondelete="CASCADE"), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    stock_id   = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    action     = Column(String(10), nullable=False)  # 'BUY' / 'SELL' / 'HOLD'
    shares     = Column(Integer)
    price      = Column(DECIMAL(10, 2))
    value      = Column(DECIMAL(18, 2))
    weight     = Column(DECIMAL(8, 4))

    __table_args__ = (
        Index("ix_backtest_positions_run", "run_id"),
    )

    run   = relationship("BacktestRun", back_populates="positions")
    stock = relationship("Stock", back_populates="backtest_positions")


class BacktestEquity(Base):
    __tablename__ = "backtest_equity"

    run_id          = Column(String(64), ForeignKey("backtest_runs.run_id", ondelete="CASCADE"), primary_key=True)
    trade_date      = Column(Date, primary_key=True)
    portfolio_value = Column(DECIMAL(18, 2))
    benchmark_value = Column(DECIMAL(18, 2))
    drawdown        = Column(DECIMAL(8, 4))

    __table_args__ = (
        Index("ix_backtest_equity_run", "run_id"),
    )

    run = relationship("BacktestRun", back_populates="equity")


class Portfolio(Base):
    __tablename__ = "portfolio"

    stock_id     = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    avg_cost     = Column(DECIMAL(18, 2), nullable=False)
    shares       = Column(Integer, nullable=False)
    is_etf       = Column(Boolean, default=False)
    updated_at   = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)
    pl_thod      = Column(Numeric(precision=53))  # DOUBLE PRECISION
    pl_pct_thod  = Column(Numeric(precision=53))
    alert_enabled = Column(Boolean, default=True)

    stock = relationship("Stock", back_populates="portfolio")


class Lot(Base):
    __tablename__ = "lots"

    id         = Column(String(64), primary_key=True)
    stock_id   = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), nullable=False)
    date       = Column(Date, nullable=False)
    shares     = Column(Integer, nullable=False)
    cost       = Column(DECIMAL(18, 2), nullable=False)
    created_at = Column(TIMESTAMP, default=datetime.now)

    __table_args__ = (
        Index("ix_lots_stock", "stock_id"),
    )

    stock = relationship("Stock", back_populates="lots")


class AlertSetting(Base):
    __tablename__ = "alert_settings"

    key          = Column(String(64), primary_key=True)
    value        = Column(String(255))
    is_sensitive = Column(Boolean, default=False)
    updated_at   = Column(TIMESTAMP, default=datetime.now)


class AlertLog(Base):
    __tablename__ = "alert_log"

    log_id         = Column(String(64), primary_key=True)
    stock_id       = Column(String(10), ForeignKey("stocks.stock_id", ondelete="SET NULL"))
    triggered_at   = Column(TIMESTAMP, default=datetime.now)
    pnl            = Column(DECIMAL(18, 2))
    pnl_pct        = Column(DECIMAL(8, 4))
    threshold_type  = Column(String(20))
    threshold_value = Column(DECIMAL(18, 2))
    avg_cost        = Column(DECIMAL(18, 2))
    current_price   = Column(DECIMAL(10, 2))
    shares          = Column(Integer)
    sent            = Column(Boolean)
    reason          = Column(String(255))

    __table_args__ = (
        Index("ix_alert_log_stock", "stock_id"),
        Index("ix_alert_log_triggered", "triggered_at"),
    )

    stock = relationship("Stock", back_populates="alert_log")


class AlertHistory(Base):
    __tablename__ = "alert_history"

    id           = Column(String(64), primary_key=True)
    rule_name    = Column(String(100), index=True)
    stock_id     = Column(String(10), ForeignKey("stocks.stock_id", ondelete="SET NULL"))
    severity     = Column(String(20), index=True)  # CRITICAL / HIGH / MEDIUM / LOW
    message      = Column(Text)
    context_data = Column(JSONB)
    triggered_at = Column(TIMESTAMP, default=datetime.now, index=True)
    resolved_at     = Column(TIMESTAMP, nullable=True, index=True)
    resolution_note = Column(Text, nullable=True)

    stock = relationship("Stock", back_populates="alert_history")


class AlertCooldown(Base):
    __tablename__ = "alert_cooldowns"

    rule_name         = Column(String(100), primary_key=True)
    last_alert_time   = Column(TIMESTAMP, default=datetime.now)
    cooldown_seconds  = Column(Integer, default=3600)


class AlertRule(Base):
    """T128: unified alert rule configuration."""
    __tablename__ = "alert_rules"

    rule_name        = Column(String(100), primary_key=True)
    enabled          = Column(Boolean, default=True)
    threshold        = Column(DECIMAL(18, 6), nullable=True)
    cooldown_seconds = Column(Integer, default=3600)
    severity         = Column(String(20), default="MEDIUM")
    description      = Column(String(255), nullable=True)
    updated_at       = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)


class RealtimePrice(Base):
    __tablename__ = "realtime_prices"

    stock_id   = Column(String(10), primary_key=True)
    close      = Column(DECIMAL(10, 2))
    trade_date = Column(Date)
    updated_at = Column(TIMESTAMP, default=datetime.now, onupdate=datetime.now)


class RealtimeQuoteModel(Base):
    __tablename__ = "realtime_quotes"

    stock_id        = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    quote_time      = Column(TIMESTAMP, primary_key=True)
    price           = Column(DECIMAL(10, 2))
    volume          = Column(BIGINT)
    bid             = Column(DECIMAL(10, 2))
    ask             = Column(DECIMAL(10, 2))
    change_amt      = Column(DECIMAL(10, 2))
    change_pct      = Column(DECIMAL(8, 4))
    is_close        = Column(Boolean, default=False)
    pe_realtime     = Column(DECIMAL(10, 2))
    pb_realtime     = Column(DECIMAL(10, 2))
    yield_realtime  = Column(DECIMAL(8, 4))
    open_price      = Column(DECIMAL(10, 2))
    high_price      = Column(DECIMAL(10, 2))
    low_price       = Column(DECIMAL(10, 2))

    __table_args__ = (
        Index("ix_realtime_quotes_time", "quote_time"),
    )

    stock = relationship("Stock", back_populates="realtime_quotes")


class IntradaySnapshot(Base):
    __tablename__ = "intraday_snapshots"

    stock_id      = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    snapshot_time = Column(TIMESTAMP, primary_key=True)
    price         = Column(DECIMAL(10, 2))
    volume        = Column(BIGINT)
    bid           = Column(DECIMAL(10, 2))
    ask           = Column(DECIMAL(10, 2))
    change_amt    = Column(DECIMAL(10, 2))
    change_pct    = Column(DECIMAL(8, 4))

    __table_args__ = (
        Index("ix_intraday_snapshots_time", "snapshot_time"),
    )

    stock = relationship("Stock", back_populates="intraday_snapshots")


class IntradayKline(Base):
    __tablename__ = "intraday_kline"

    stock_id    = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    k_time      = Column(TIMESTAMP, primary_key=True)
    period_min  = Column(Integer, primary_key=True, default=60)
    open        = Column(DECIMAL(10, 2))
    high        = Column(DECIMAL(10, 2))
    low         = Column(DECIMAL(10, 2))
    close       = Column(DECIMAL(10, 2))
    volume      = Column(BIGINT)

    __table_args__ = (
        Index("ix_intraday_kline_time", "k_time"),
        Index("ix_intraday_kline_stock_time", "stock_id", "k_time"),
    )

    stock = relationship("Stock", back_populates="intraday_kline")


class OperationLog(Base):
    __tablename__ = "operation_logs"

    id         = Column(String(64), primary_key=True)
    module     = Column(String(50), nullable=False, index=True)
    event      = Column(String(100), nullable=False)
    severity   = Column(String(20), nullable=False, index=True)  # INFO / WARN / ERROR / CRITICAL
    created_at = Column(TIMESTAMP, default=datetime.now, index=True)


class StrategyConfigHistory(Base):
    __tablename__ = "strategy_config_history"

    config_id       = Column(Integer, Sequence("seq_strategy_config_history_id"), primary_key=True)
    changed_at      = Column(TIMESTAMP, default=datetime.now, index=True)
    weights         = Column(JSONB)
    advanced_params = Column(JSONB)
    guru_config     = Column(JSONB)
    universe_config = Column(JSONB)
    changed_by      = Column(String(50), default="user")
    note            = Column(String(255))


class GuruScore(Base):
    __tablename__ = "guru_scores"

    score_date      = Column(Date, primary_key=True)
    stock_id        = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    guru            = Column(String(50), primary_key=True)  # 'piotroski' / 'graham' / etc.
    score           = Column(DECIMAL(8, 4))
    pass_filter     = Column(Boolean)
    criteria_detail = Column(JSONB)

    __table_args__ = (
        Index("ix_guru_scores_guru", "guru"),
        Index("ix_guru_scores_date", "score_date"),
    )

    stock = relationship("Stock", back_populates="guru_scores")


class InstitutionalFlow(Base):
    __tablename__ = "institutional_flows"

    stock_id                = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    trade_date              = Column(Date, primary_key=True)
    market                  = Column(String(10), nullable=False)
    foreign_investors_net   = Column(BIGINT)
    sity_investors_net      = Column(BIGINT)
    dealer_net              = Column(BIGINT)
    dealer_proprietary_net  = Column(BIGINT)
    dealer_hedge_net        = Column(BIGINT)
    total_net               = Column(BIGINT)

    __table_args__ = (
        Index("ix_institutional_flows_date", "trade_date"),
        Index("ix_institutional_flows_stock", "stock_id"),
    )

    stock = relationship("Stock", back_populates="institutional_flows")


class InstitutionalHolding(Base):
    __tablename__ = "institutional_holdings"

    stock_id           = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    snapshot_date      = Column(Date, primary_key=True)
    foreign_holding_pct = Column(DECIMAL(10, 4))
    trust_holding_pct   = Column(DECIMAL(10, 4))
    dealer_holding_pct  = Column(DECIMAL(10, 4))
    total_inst_pct      = Column(DECIMAL(10, 4))
    data_source        = Column(String(20), default="finmind")

    __table_args__ = (
        Index("ix_institutional_holdings_date", "snapshot_date"),
        Index("ix_institutional_holdings_stock", "stock_id"),
    )

    stock = relationship("Stock", back_populates="institutional_holdings")


class IngestionTracker(Base):
    __tablename__ = "ingestion_tracker"

    stock_id     = Column(String(10), ForeignKey("stocks.stock_id", ondelete="CASCADE"), primary_key=True)
    dataset      = Column(String(50), primary_key=True)  # 'daily_prices' / 'financials' / etc.
    bucket       = Column(Integer)
    last_updated = Column(Date)
    last_status  = Column(String(20))  # 'success' / 'error' / 'skip'
    error_msg    = Column(String(500))

    __table_args__ = (
        Index("ix_ingestion_tracker_dataset", "dataset"),
        Index("ix_ingestion_tracker_updated", "last_updated"),
    )

    stock = relationship("Stock", back_populates="ingestion_tracker")
