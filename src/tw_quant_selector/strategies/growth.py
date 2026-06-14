from __future__ import annotations
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import text

from tw_quant_selector.strategies.base import BaseStrategy, register_strategy, safe_zscore


@register_strategy
class GrowthStrategy(BaseStrategy):
    name = "growth"

    def __init__(self, rev_weight: float = 0.6, eps_weight: float = 0.4, rev_months: int = 3):
        self.rev_weight = rev_weight
        self.eps_weight = eps_weight
        self.rev_months = rev_months

    def get_required_data(self) -> list[str]:
        return ["monthly_revenue", "financials"]

    def compute_score(self, universe: list[str], as_of_date: date, db=None) -> dict[str, float]:
        scores: dict[str, float] = {}
        for sid in universe:
            rev = pd.DataFrame(
                db.execute(text("""SELECT revenue, revenue_yoy, year_month, announcement_date
                   FROM monthly_revenue
                   WHERE stock_id = :sid AND announcement_date <= :as_of_date
                   ORDER BY year_month DESC LIMIT :limit"""),
                    {"sid": sid, "as_of_date": as_of_date, "limit": self.rev_months + 1}).fetchall(),
                columns=["revenue", "revenue_yoy", "year_month", "announcement_date"])

            eps_rows = pd.DataFrame(
                db.execute(text("""SELECT eps, announcement_date
                   FROM financials
                   WHERE stock_id = :sid AND announcement_date <= :as_of_date
                   ORDER BY year_quarter DESC LIMIT 2"""),
                    {"sid": sid, "as_of_date": as_of_date}).fetchall(),
                columns=["eps", "announcement_date"])

            components = []

            if len(rev) >= 2:
                yoy_vals = rev["revenue_yoy"].dropna().tail(self.rev_months)
                if not yoy_vals.empty:
                    rev_score = safe_zscore(yoy_vals.astype(float).values)[-1] if len(yoy_vals) > 1 else float(yoy_vals.astype(float).mean())
                    components.append(rev_score * self.rev_weight)

            if len(eps_rows) >= 2:
                eps_q = eps_rows["eps"].iloc[0]
                eps_last = eps_rows["eps"].iloc[1]
                if eps_q is not None and eps_last is not None and eps_last != 0:
                    eps_qoq = (float(eps_q) / float(eps_last)) - 1
                    eps_score = safe_zscore(np.array([eps_qoq]))[0]
                    components.append(eps_score * self.eps_weight)

            if components:
                scores[sid] = float(np.sum(components))

        if not scores:
            return {}
        vals = np.array(list(scores.values()))
        if np.std(vals) == 0:
            return {k: 0.0 for k in scores}
        z = safe_zscore(vals)
        return {sid: float(z[i]) for i, sid in enumerate(scores)}
