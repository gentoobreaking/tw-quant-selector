from __future__ import annotations
from datetime import date, timedelta
from typing import Any
import numpy as np
import structlog

from tw_quant_selector.strategies.base import safe_zscore
from tw_quant_selector.strategies.guru_filters import (
    get_guru_filter, list_guru_filters, GuruFilter,
)

log = structlog.get_logger()

GURU_WEIGHT_PREFIX = "guru_"
DEFAULT_GURU_WEIGHT = 0.20


def compute_guru_score(db, stock_id: str, guru: str, as_of_date: date) -> float:
    try:
        guru_filter = get_guru_filter(guru)
    except KeyError:
        return 0.0
    try:
        results = guru_filter.get_pass_fail(db, stock_id, as_of_date)
    except Exception:
        return 0.0
    if not results:
        return 0.0
    passed = sum(1 for v in results.values() if v)
    total = max(len(results), 1)
    return round(passed / total * 100, 2)


def compute_guru_scores(db, universe: list[str], as_of_date: date) -> dict[str, dict[str, float]]:
    all_scores: dict[str, dict[str, float]] = {}
    for stock_id in universe:
        guru_scores: dict[str, float] = {}
        for guru in list_guru_filters():
            guru_scores[guru] = compute_guru_score(db, stock_id, guru, as_of_date)
        all_scores[stock_id] = guru_scores
    return all_scores


def compute_composite_guru_score(
    db, stock_id: str, as_of_date: date,
    guru_weights: Optional[dict[str, float]] = None,
) -> float:
    scores = compute_guru_scores(db, [stock_id], as_of_date).get(stock_id, {})
    if not scores:
        return 0.0
    w = guru_weights or {g: 1.0 / max(len(list_guru_filters()), 1) for g in list_guru_filters()}
    weighted = sum(scores.get(g, 0) * w.get(g, 0) for g in list_guru_filters())
    return round(weighted, 4)


def save_guru_scores(db, as_of_date: date, scores: dict[str, dict[str, float]]):
    with db.connection(read_only=False) as conn:
        for stock_id, guru_scores in scores.items():
            for guru, score in guru_scores.items():
                conn.execute(
                    """INSERT INTO guru_scores (score_date, stock_id, guru, score)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (score_date, stock_id, guru)
                       DO UPDATE SET score = excluded.score""",
                    [as_of_date, stock_id, guru, score],
                )
        conn.commit()
    log.info("guru_scores.saved", date=str(as_of_date), stocks=len(scores))


def run_guru_scoring(db, as_of_date: Optional[date] = None):
    from tw_quant_selector.portfolio.universe import get_universe
    as_of = as_of_date or date.today()
    universe = get_universe(db, as_of)
    all_ids = [s["stock_id"] for s in universe["stocks"]] + [s["stock_id"] for s in universe["etfs"]]
    scores = compute_guru_scores(db, all_ids, as_of)
    save_guru_scores(db, as_of, scores)
    return len(scores)
