from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel


class Stock(BaseModel):
    stock_id: str
    stock_name: str
    market: str
    industry: Optional[str] = None
    list_date: Optional[date] = None
    delist_date: Optional[date] = None
    is_etf: bool = False


class DailyPrice(BaseModel):
    stock_id: str
    trade_date: date
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    volume: Optional[int] = None
    amount: Optional[Decimal] = None
    adj_factor: Optional[Decimal] = None
    adj_close: Optional[Decimal] = None


class MonthlyRevenue(BaseModel):
    stock_id: str
    year_month: str
    revenue: Optional[int] = None
    revenue_yoy: Optional[Decimal] = None
    announcement_date: Optional[date] = None


class FinancialStatement(BaseModel):
    stock_id: str
    year_quarter: str
    revenue: Optional[int] = None
    gross_profit: Optional[int] = None
    operating_income: Optional[int] = None
    net_income: Optional[int] = None
    eps: Optional[Decimal] = None
    roe: Optional[Decimal] = None
    roa: Optional[Decimal] = None
    gross_margin: Optional[Decimal] = None
    operating_margin: Optional[Decimal] = None
    debt_to_equity: Optional[Decimal] = None
    announcement_date: Optional[date] = None


class Valuation(BaseModel):
    stock_id: str
    trade_date: date
    pe_ratio: Optional[Decimal] = None
    pb_ratio: Optional[Decimal] = None
    dividend_yield: Optional[Decimal] = None
    market_cap: Optional[Decimal] = None


class Signal(BaseModel):
    signal_date: date
    stock_id: str
    strategy: str
    score: Optional[Decimal] = None
    rank: Optional[int] = None
    is_selected: Optional[bool] = None


class BacktestRun(BaseModel):
    run_id: str
    run_at: Optional[datetime] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    strategy_config: Optional[dict] = None
    total_return: Optional[Decimal] = None
    cagr: Optional[Decimal] = None
    sharpe: Optional[Decimal] = None
    max_drawdown: Optional[Decimal] = None
    calmar: Optional[Decimal] = None
    turnover: Optional[Decimal] = None
    result_path: Optional[str] = None


class BacktestPosition(BaseModel):
    run_id: str
    trade_date: date
    stock_id: str
    action: str
    shares: Optional[int] = None
    price: Optional[Decimal] = None
    value: Optional[Decimal] = None
    weight: Optional[Decimal] = None
