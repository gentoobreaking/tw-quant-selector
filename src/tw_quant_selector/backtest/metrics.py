from __future__ import annotations
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, Any
import numpy as np

RISK_FREE_RATE = 0.015


def compute_metrics(
    portfolio_values: list[tuple[date, Decimal]],
    initial_capital: Decimal,
    benchmark_returns: Optional[list[float]] = None,
) -> dict[str, Any]:
    if not portfolio_values:
        return {}

    values = [float(v) for _, v in portfolio_values]
    init_val = float(initial_capital)
    total_return = (values[-1] / init_val) - 1 if init_val > 0 else 0

    years = (portfolio_values[-1][0] - portfolio_values[0][0]).days / 365.25
    cagr = ((values[-1] / init_val) ** (1 / years)) - 1 if years > 0 else total_return

    daily_returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            daily_returns.append((values[i] / values[i - 1]) - 1)
    daily_returns = np.array(daily_returns) if daily_returns else np.array([0])

    ann_std = float(np.std(daily_returns, ddof=1)) * np.sqrt(252) if len(daily_returns) > 1 else 0
    ann_ret = float(np.mean(daily_returns)) * 252 if len(daily_returns) > 0 else 0
    sharpe = (ann_ret - RISK_FREE_RATE) / ann_std if ann_std > 0 else 0

    peak = values[0]
    max_dd = 0
    dd_start = portfolio_values[0][0]
    dd_end = portfolio_values[0][0]
    peak_date = portfolio_values[0][0]

    for val, d in zip(values, [p[0] for p in portfolio_values]):
        if val > peak:
            peak = val
            peak_date = d
        dd = (val - peak) / peak if peak > 0 else 0
        if dd < max_dd:
            max_dd = dd
            dd_start = peak_date
            dd_end = d

    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    neg_returns = daily_returns[daily_returns < 0]
    downside_std = float(np.std(neg_returns, ddof=1)) * np.sqrt(252) if len(neg_returns) > 1 else 0
    sortino = (ann_ret - RISK_FREE_RATE) / downside_std if downside_std > 0 else 0

    var_95 = float(np.percentile(daily_returns, 5)) if len(daily_returns) > 0 else 0

    total_trades = len(portfolio_values)
    holding_days = (portfolio_values[-1][0] - portfolio_values[0][0]).days
    turnover = total_trades / years if years > 0 else 0

    return {
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "ann_volatility": round(ann_std, 4),
        "sharpe": round(sharpe, 4),
        "sortino": round(sortino, 4),
        "calmar": round(calmar, 4),
        "max_drawdown": round(max_dd, 4),
        "max_drawdown_start": dd_start.isoformat(),
        "max_drawdown_end": dd_end.isoformat(),
        "var_95": round(var_95, 4),
        "turnover": round(turnover, 2),
        "avg_holding_days": round(holding_days / max(total_trades, 1), 1),
        "n_trades": total_trades,
    }
