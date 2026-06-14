"""Technical indicator calculations for intraday K-line data.

Provides SMA (Simple Moving Average) and KD (Stochastic Oscillator)
for use by the alerting system.
"""

from __future__ import annotations
from typing import Optional


def compute_sma(values: list[float], period: int) -> list[Optional[float]]:
    """Simple Moving Average.

    Args:
        values: list of prices (oldest first)
        period: lookback window

    Returns:
        list aligned with input; first (period-1) entries are None
    """
    result: list[Optional[float]] = [None] * len(values)
    if len(values) < period or period <= 0:
        return result
    window_sum = sum(values[:period])
    result[period - 1] = window_sum / period
    for i in range(period, len(values)):
        window_sum += values[i] - values[i - period]
        result[i] = window_sum / period
    return result


def compute_kd(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 60,
    k1: int = 3,
    d1: int = 3,
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """Stochastic Oscillator (KD).

    Standard formula:
      RSV = (Close - LLn) / (HHn - LLn) * 100
      K = 2/3 * K_prev + 1/3 * RSV
      D = 2/3 * D_prev + 1/3 * K

    Args:
        highs: high prices (oldest first)
        lows: low prices (oldest first)
        closes: close prices (oldest first)
        n: RSV lookback period
        k1: K smoothing (default 3)
        d1: D smoothing (default 3, typically same as k1)

    Returns:
        (rsv_list, k_list, d_list) aligned with input; leading None values
    """
    length = len(closes)
    rsv: list[Optional[float]] = [None] * length
    k_vals: list[Optional[float]] = [None] * length
    d_vals: list[Optional[float]] = [None] * length

    if length < n or n <= 0:
        return rsv, k_vals, d_vals

    for i in range(n - 1, length):
        hh = max(highs[i - n + 1: i + 1])
        ll = min(lows[i - n + 1: i + 1])
        if hh == ll:
            rsv[i] = 50.0
        else:
            rsv[i] = (closes[i] - ll) / (hh - ll) * 100.0

    # K: first K = first RSV
    first_idx = n - 1
    k_vals[first_idx] = rsv[first_idx]
    for i in range(first_idx + 1, length):
        if rsv[i] is not None and k_vals[i - 1] is not None:
            k_vals[i] = (2.0 / 3.0) * k_vals[i - 1] + (1.0 / 3.0) * rsv[i]

    # D: first D = first K
    d_vals[first_idx] = k_vals[first_idx]
    for i in range(first_idx + 1, length):
        if k_vals[i] is not None and d_vals[i - 1] is not None:
            d_vals[i] = (2.0 / 3.0) * d_vals[i - 1] + (1.0 / 3.0) * k_vals[i]

    return rsv, k_vals, d_vals
