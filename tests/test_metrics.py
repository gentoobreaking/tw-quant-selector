from datetime import date
from decimal import Decimal
from tw_quant_selector.backtest.metrics import compute_metrics


def test_positive_return():
    values = [
        (date(2024, 1, 1), Decimal("1000000")),
        (date(2025, 1, 1), Decimal("1200000")),
    ]
    m = compute_metrics(values, Decimal("1000000"))
    assert m["total_return"] > 0
    assert m["cagr"] > 0
    assert m["n_trades"] == 2


def test_negative_return():
    values = [
        (date(2024, 1, 1), Decimal("1000000")),
        (date(2025, 1, 1), Decimal("800000")),
    ]
    m = compute_metrics(values, Decimal("1000000"))
    assert m["total_return"] < 0
    assert m["max_drawdown"] < 0


def test_empty_values():
    m = compute_metrics([], Decimal("1000000"))
    assert m == {}
