from __future__ import annotations
from typing import Optional, Any, Union
import numpy as np
from datetime import date, timedelta
from scipy.stats import pearsonr


def compute_factor_correlation(
    db, as_of_date: Optional[date] = None,
    lookback_days: int = 252,
) -> dict[str, dict[str, float]]:
    as_of = as_of_date or date.today()
    start = as_of - timedelta(days=int(lookback_days * 1.5))

    rows = db.execute(
        """SELECT signal_date, stock_id, strategy, score
           FROM signals
           WHERE signal_date >= ? AND signal_date <= ?
           ORDER BY signal_date, stock_id, strategy""",
        [start, as_of],
    ).fetchall()

    strat_scores: dict[str, dict[str, float]] = {}
    for row in rows:
        d, sid, strategy, score = row
        if strategy == "composite" or score is None:
            continue
        key = f"{d}|{sid}"
        if key not in strat_scores:
            strat_scores[key] = {}
        strat_scores[key][strategy] = float(score)

    strategies = sorted({s for scores in strat_scores.values() for s in scores})
    if len(strategies) < 2:
        return {s: {s2: 0.0 for s2 in strategies} for s in strategies}

    matrix: dict[str, dict[str, float]] = {s: {} for s in strategies}
    for i, s1 in enumerate(strategies):
        for s2 in strategies:
            if s2 in matrix[s1]:
                continue
            pairs = []
            for scores in strat_scores.values():
                if s1 in scores and s2 in scores:
                    pairs.append((scores[s1], scores[s2]))
            if len(pairs) < 30:
                corr = 0.0
            else:
                arr = np.array(pairs)
                v1, v2 = arr[:, 0].astype(float), arr[:, 1].astype(float)
                if np.std(v1) < 1e-12 or np.std(v2) < 1e-12:
                    corr = 0.0
                else:
                    corr, _ = pearsonr(v1, v2)
                    corr = round(corr, 4)
            matrix[s1][s2] = corr
            if s1 != s2:
                if s2 not in matrix:
                    matrix[s2] = {}
                matrix[s2][s1] = corr

    return matrix
