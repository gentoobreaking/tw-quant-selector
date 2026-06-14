from __future__ import annotations
from datetime import date
from typing import Any
import numpy as np

from tw_quant_selector.strategies.base import BaseStrategy, register_strategy, safe_zscore
from tw_quant_selector.strategies.guru_scoring import compute_guru_score, list_guru_filters

@register_strategy
class GuruStrategy(BaseStrategy):
    name = "guru"

    def __init__(self, selected_guru: str = "buffett"):
        self.selected_guru = selected_guru

    def get_required_data(self) -> list[str]:
        return ["financials", "valuations", "stocks"]

    def compute_score(self, universe: list[str], as_of_date: date, db=None) -> dict[str, float]:
        scores: dict[str, float] = {}
        for sid in universe:
            score = compute_guru_score(db, sid, self.selected_guru, as_of_date)
            scores[sid] = float(score)
        
        if not scores:
            return {}
        
        # Z-score normalization for consistency with other strategies
        vals = np.array(list(scores.values()))
        if np.std(vals) < 1e-12:
            return {sid: 0.0 for sid in scores}
            
        z = safe_zscore(vals)
        return {sid: float(z[i]) for i, sid in enumerate(scores)}
