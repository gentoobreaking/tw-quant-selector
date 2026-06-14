from __future__ import annotations
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional, Any
import uuid, json
import structlog

from tw_quant_selector.data.database import Database
from tw_quant_selector.portfolio.costs import calc_sell_cost
from tw_quant_selector.portfolio.portfolio import Portfolio, INITIAL_CAPITAL
from tw_quant_selector.strategies.combiner import compute_composite_scores, DEFAULT_WEIGHTS
from tw_quant_selector.backtest.metrics import compute_metrics

log = structlog.get_logger()


def _get_price(db, stock_id: str, trade_date: date) -> Optional[Decimal]:
    row = db.execute(
        """SELECT close FROM daily_prices
           WHERE stock_id = ? AND trade_date = ?""",
        [stock_id, trade_date],
    ).fetchone()
    return Decimal(str(row[0])) if row and row[0] else None


def _historical_universe(db, as_of_date: date) -> list[str]:
    rows = db.execute(
        """SELECT stock_id FROM stocks
           WHERE list_date <= ?
           AND (delist_date IS NULL OR delist_date > ?)""",
        [as_of_date, as_of_date],
    ).fetchall()
    return [r[0] for r in rows]


def _rebalance_dates(start: date, end: date) -> list[date]:
    """Return all weekdays (Mon-Fri) for daily check, Monday for rebalance check."""
    dates: list[date] = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return dates


def _sell_position(
    portfolio: Portfolio,
    stock_id: str,
    current_price: Decimal,
    as_of_date: date,
) -> dict[str, Any]:
    """Sell all shares of a position and add proceeds to cash."""
    pos = portfolio.positions.pop(stock_id)
    proceeds = calc_sell_cost(current_price, pos.shares, pos.is_etf)
    portfolio.cash += proceeds
    return {
        "stock_id": stock_id,
        "action": "SELL",
        "shares": pos.shares,
        "price": current_price,
        "value": proceeds,
        "date": as_of_date,
    }


def _check_stop_loss_take_profit(
    portfolio: Portfolio,
    prices: dict[str, Decimal],
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Check all positions for stop-loss (-10%) and take-profit (+20%).

    Returns list of SELL trades for any triggered positions.
    """
    trades: list[dict[str, Any]] = []
    for sid in list(portfolio.positions.keys()):
        price = prices.get(sid)
        if not price or price <= 0:
            continue
        pos = portfolio.positions[sid]
        ret = (price - pos.avg_cost) / pos.avg_cost
        if ret <= Decimal("-0.10") or ret >= Decimal("0.20"):
            trades.append(_sell_position(portfolio, sid, price, as_of_date))
    return trades


def run_backtest(
    db: Database,
    start_date: date,
    end_date: Optional[date] = None,
    run_id: Optional[str] = None,
    initial_capital: Decimal = INITIAL_CAPITAL,
    strategy_weights: Optional[dict[str, float]] = None,
    benchmark: str = "0050",
    custom_universe: Optional[list[str]] = None,
) -> dict[str, Any]:
    run_id = run_id or str(uuid.uuid4())
    end_date = end_date or date.today()
    weights = strategy_weights or DEFAULT_WEIGHTS
    portfolio = Portfolio(initial_capital=initial_capital)
    all_trades: list[dict] = []
    benchmark_returns: list[float] = []
    portfolio_values: list[tuple[date, Decimal]] = []

    trading_dates = _rebalance_dates(start_date, end_date)

    for i, tdate in enumerate(trading_dates):
        is_rebalance = tdate.weekday() == 0
        universe = _historical_universe(db, tdate)

        if not universe:
            continue

        prices: dict[str, Decimal] = {}
        industries: dict[str, str] = {}

        if is_rebalance:
            # If custom universe is provided, pass it to compute_composite_scores via strategy_params
            strategy_params = {}
            if custom_universe:
                strategy_params["custom_universe"] = custom_universe
                
            result = compute_composite_scores(db, tdate, weights, top_n_stocks=20, top_n_etfs=3, strategy_params=strategy_params)
            stock_scores = {s["stock_id"]: s["score"] for s in result.get("stocks", [])}
            etf_scores = {s["stock_id"]: s["score"] for s in result.get("etfs", [])}

            all_ids = list(stock_scores.keys()) + list(etf_scores.keys())
            for sid in all_ids:
                p = _get_price(db, sid, tdate)
                if p:
                    prices[sid] = p
                ind = db.execute(
                    "SELECT industry FROM stocks WHERE stock_id = ?", [sid]
                ).fetchone()
                if ind:
                    industries[sid] = ind[0]

            if not prices:
                continue

            new_stocks = [{"stock_id": sid, "score": sc}
                           for sid, sc in sorted(stock_scores.items(), key=lambda x: -x[1])[:20]]
            new_etfs = [{"stock_id": sid, "score": sc}
                         for sid, sc in sorted(etf_scores.items(), key=lambda x: -x[1])[:3]]

            trades = portfolio.rebalance(new_stocks, new_etfs, prices, industries, tdate)
            for t in trades:
                t["run_id"] = run_id
                all_trades.append(t)
        else:
            for sid in list(portfolio.positions.keys()):
                p = _get_price(db, sid, tdate)
                if p:
                    prices[sid] = p
            if prices:
                sl_trades = _check_stop_loss_take_profit(portfolio, prices, tdate)
                for t in sl_trades:
                    t["run_id"] = run_id
                    all_trades.append(t)

        val_prices: dict[str, Decimal] = {}
        for sid in list(portfolio.positions.keys()):
            p = _get_price(db, sid, tdate)
            if p:
                val_prices[sid] = p
        portfolio_values.append((tdate, portfolio.total_value(val_prices)))

        bm_price = _get_price(db, benchmark, tdate)
        if bm_price:
            benchmark_returns.append(float(bm_price))

        if (i + 1) % 50 == 0:
            log.info("backtest.progress", run_id=run_id, date=str(tdate), progress=f"{i + 1}/{len(trading_dates)}")

    metrics = compute_metrics(portfolio_values, initial_capital, benchmark_returns)
    metrics["run_id"] = run_id
    metrics["start_date"] = start_date.isoformat()
    metrics["end_date"] = end_date.isoformat()
    metrics["strategy_config"] = weights
    metrics["benchmark"] = benchmark

    _save_backtest(db, run_id, metrics)
    _save_trades(db, all_trades)
    _save_equity(db, run_id, portfolio_values, benchmark_returns)

    log.info("backtest.completed", run_id=run_id,
             total_return=metrics.get("total_return"), sharpe=metrics.get("sharpe"))
    return metrics


def _save_backtest(db, run_id: str, metrics: dict):
    with db.connection(read_only=False) as conn:
        conn.execute(
            """INSERT INTO backtest_runs
               (run_id, run_at, start_date, end_date, strategy_config,
                total_return, cagr, sharpe, max_drawdown, calmar, turnover, benchmark)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [run_id, datetime.now(), metrics.get("start_date"), metrics.get("end_date"),
             json.dumps(metrics.get("strategy_config", {})),
             metrics.get("total_return"), metrics.get("cagr"),
             metrics.get("sharpe"), metrics.get("max_drawdown"),
             metrics.get("calmar"), metrics.get("turnover"),
             metrics.get("benchmark", "0050")],
        )
        conn.commit()


def _save_trades(db, trades: list[dict]):
    if not trades:
        return
    with db.connection(read_only=False) as conn:
        for t in trades:
            conn.execute(
                """INSERT INTO backtest_positions
                   (run_id, trade_date, stock_id, action, shares, price, value, weight)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                [t.get("run_id"), t.get("date"), t.get("stock_id"), t.get("action"),
                 t.get("shares"), t.get("price"), t.get("value"), None],
            )
        conn.commit()


def _save_equity(db, run_id: str, portfolio_values: list[tuple[date, Decimal]], benchmark_prices: list[float]):
    if not portfolio_values:
        return
    
    # Normalize benchmark to match initial capital
    initial_cap = float(portfolio_values[0][1])
    normalized_benchmark = []
    if benchmark_prices:
        first_bm = benchmark_prices[0]
        normalized_benchmark = [(p / first_bm) * initial_cap for p in benchmark_prices]
    
    # Calculate drawdown curve
    peak = 0.0
    with db.connection(read_only=False) as conn:
        for i, (d, val) in enumerate(portfolio_values):
            v = float(val)
            if v > peak:
                peak = v
            dd = (v - peak) / peak if peak > 0 else 0
            bm = normalized_benchmark[i] if i < len(normalized_benchmark) else None
            
            conn.execute(
                """INSERT INTO backtest_equity (run_id, trade_date, portfolio_value, benchmark_value, drawdown)
                   VALUES (?, ?, ?, ?, ?)""",
                [run_id, d, v, bm, dd]
            )
        conn.commit()

