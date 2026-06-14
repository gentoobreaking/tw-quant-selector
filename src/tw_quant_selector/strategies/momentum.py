from __future__ import annotations
from datetime import date, timedelta
from math import log

import numpy as np
import pandas as pd

from tw_quant_selector.strategies.base import BaseStrategy, register_strategy, safe_zscore


@register_strategy
class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(self, lookback_long: int = 252, lookback_short: int = 22, min_data_days: int = 252):
        self.lookback_long = lookback_long
        self.lookback_short = lookback_short
        self.min_data_days = min_data_days

    def get_required_data(self) -> list[str]:
        return ["daily_prices"]

    def compute_score(self, universe: list[str], as_of_date: date, dp=None) -> dict[str, float]:
        scores: dict[str, float] = {}
        start = as_of_date - timedelta(days=int(self.lookback_long * 1.5))

        for sid in universe:
            prices = dp.get_daily_prices(sid, start, as_of_date) if dp else None
            if prices is None or prices.empty:
                continue
            close = prices["close"].values
            volume = prices.get("volume", pd.Series([0] * len(prices))).values

            if len(close) < self.min_data_days:
                continue

            p0 = close[-self.lookback_short] if len(close) >= self.lookback_short else close[0]
            p1 = close[0]
            momentum = (p0 / p1) - 1 if p1 != 0 else 0

            vol_window = volume[-20:] if len(volume) >= 20 else volume
            vol_clean = vol_window[~np.isnan(vol_window)]
            avg_vol = float(np.mean(vol_clean)) if len(vol_clean) > 0 else 1.0
            liq_weight = log(max(avg_vol, 1))

            scores[sid] = momentum * liq_weight

        if not scores:
            return {}

        vals = np.array(list(scores.values()))
        if np.std(vals) == 0:
            return {k: 0.0 for k in scores}

        z = safe_zscore(vals)
        return {sid: float(z[i]) for i, sid in enumerate(scores)}
