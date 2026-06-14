from __future__ import annotations
from typing import Optional, Any, Union
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import text

from tw_quant_selector.strategies.base import BaseStrategy, register_strategy, safe_zscore


@register_strategy
class QualityStrategy(BaseStrategy):
    name = "quality"

    def __init__(self, roe_weight: float = 0.35, leverage_weight: float = 0.21,
                 stability_weight: float = 0.14, fscore_weight: float = 0.30,
                 lookback_quarters: int = 4):
        self.roe_weight = roe_weight
        self.leverage_weight = leverage_weight
        self.stability_weight = stability_weight
        self.fscore_weight = fscore_weight
        self.lookback_quarters = lookback_quarters

    def get_required_data(self) -> list[str]:
        return ["financials", "guru_scores"]

    def _guru_fscore(self, sid: str, as_of_date: date, db) -> Optional[float]:
        """Fetch the latest Piotroski F-Score from guru_scores."""
        row = db.execute(
            text("""SELECT score FROM guru_scores
               WHERE stock_id = :sid AND guru = 'piotroski' AND score_date <= :as_of_date
               ORDER BY score_date DESC LIMIT 1"""),
            {"sid": sid, "as_of_date": as_of_date},
        ).fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None

    def compute_score(self, universe: list[str], as_of_date: date, db=None) -> dict[str, float]:
        # Phase 1: original quality sub-scores
        base_scores: dict[str, float] = {}
        f_scores: dict[str, Optional[float]] = {}
        for sid in universe:
            rows = pd.DataFrame(
                db.execute(text("""SELECT roe, debt_to_equity, gross_margin
                   FROM financials
                   WHERE stock_id = :sid AND announcement_date <= :as_of_date
                   ORDER BY year_quarter DESC LIMIT :lookback"""),
                    {"sid": sid, "as_of_date": as_of_date, "lookback": self.lookback_quarters}).fetchall(),
                columns=["roe", "debt_to_equity", "gross_margin"])

            if rows.empty or len(rows) < self.lookback_quarters:
                continue

            roe_vals = rows["roe"].dropna()
            if roe_vals.empty:
                continue

            roe_score = safe_zscore(roe_vals.astype(float).values)[-1] if len(roe_vals) > 1 else 0.0

            dte = rows["debt_to_equity"].iloc[0]
            lev_score = safe_zscore(np.array([-float(dte)]))[0] if dte is not None else 0.0

            gm = rows["gross_margin"].dropna()
            gp_std = float(gm.astype(float).std()) if len(gm) > 1 else 0
            gp_stab = safe_zscore(np.array([-gp_std]))[0]

            score = (roe_score * self.roe_weight
                     + lev_score * self.leverage_weight
                     + gp_stab * self.stability_weight)
            base_scores[sid] = score
            f_scores[sid] = self._guru_fscore(sid, as_of_date, db)

        if not base_scores:
            return {}

        # Normalize base scores
        bvals = np.array(list(base_scores.values()))
        if np.std(bvals) > 0:
            base_z = {sid: float(z) for sid, z in zip(base_scores, safe_zscore(bvals))}
        else:
            base_z = {sid: 0.0 for sid in base_scores}

        # Normalize F-Scores (0-9 scale → z-score)
        f_vals = np.array([v for v in f_scores.values() if v is not None])
        has_fscore = f_vals.size > 0 and np.std(f_vals) > 0
        if has_fscore:
            f_zmap: dict[str, float] = {}
            f_zvals = safe_zscore(f_vals)
            idx = 0
            for sid in base_scores:
                fv = f_scores.get(sid)
                if fv is not None:
                    f_zmap[sid] = float(f_zvals[idx])
                    idx += 1
                else:
                    f_zmap[sid] = 0.0
        else:
            f_zmap = {sid: 0.0 for sid in base_scores}

        # Combine: final = base + fscore
        combined = {}
        for sid in base_scores:
            combined[sid] = base_z[sid] * (1 - self.fscore_weight) + f_zmap[sid] * self.fscore_weight

        # Re-normalize final scores
        cvals = np.array(list(combined.values()))
        if np.std(cvals) == 0:
            return {k: 0.0 for k in combined}
        z = safe_zscore(cvals)
        return {sid: float(z[i]) for i, sid in enumerate(combined)}
