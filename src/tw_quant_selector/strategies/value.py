from __future__ import annotations
from datetime import date, timedelta
from math import log

import numpy as np

from tw_quant_selector.strategies.base import BaseStrategy, register_strategy, safe_zscore


@register_strategy
class ValueStrategy(BaseStrategy):
    name = "value"

    def __init__(self, max_pb: float = 30, max_pe: float = 100, min_yield: float = 0):
        self.max_pb = max_pb
        self.max_pe = max_pe
        self.min_yield = min_yield

    def get_required_data(self) -> list[str]:
        return ["valuations"]

    def compute_score(self, universe: list[str], as_of_date: date, db=None) -> dict[str, float]:
        raw_scores: dict[str, float] = {}
        
        # 第一步：计算所有股票的原始分数（不做 zscore）
        for sid in universe:
            row = db.execute(
                """SELECT pe_ratio, pb_ratio, dividend_yield
                   FROM valuations
                   WHERE stock_id = ? AND trade_date <= ?
                   ORDER BY trade_date DESC LIMIT 1""",
                [sid, as_of_date],
            ).fetchone()
            if not row:
                continue

            pb, pe, div_yield = row
            components = []

            # 价值策略：低 PB/PE 好，高殖利率好
            # 所以用负号：越低越好 → zscore 越高分
            if pb is not None and 0 < pb <= self.max_pb:
                components.append(-log(float(pb)))  # 低 PB → 高分
            if pe is not None and pe > 0 and pe <= self.max_pe:
                components.append(-log(float(pe)))  # 低 PE → 高分
            if div_yield is not None and div_yield >= self.min_yield:
                components.append(float(div_yield))  # 高殖利率 → 高分

            if components:
                raw_scores[sid] = float(np.mean(components))

        if not raw_scores:
            return {}

        # 第二步：统一做 zscore 标准化
        vals = np.array(list(raw_scores.values()))
        if np.std(vals) == 0:
            return {k: 0.0 for k in raw_scores}

        z = safe_zscore(vals)
        return {sid: float(z[i]) for i, sid in enumerate(raw_scores)}
