"""Performance benchmarks for vectorized operations.

All benchmarks simulate the full market (~1,100 stocks) and assert
that vectorized operations complete within 50ms.
"""

import time
import unittest
import numpy as np
import pandas as pd

from tw_quant_selector.monitoring.alerting import (
    check_volume_spike,
    check_high_vol_no_move,
    check_turnover_monster,
    check_intraday_volatility,
    check_industry_momentum,
    check_against_trend,
    check_low_price_junk_rally,
    check_etf_premium_discount,
    check_whale_move,
    check_active_etf_hype,
)


def _make_simulated_market(n_stocks: int = 1100) -> pd.DataFrame:
    """Generate a simulated intraday snapshots DataFrame.

    Each of the n_stocks gets ~50 intraday snapshots, for a total of ~55K rows.
    """
    n_per_stock = 50
    np.random.seed(42)
    rows: list[dict] = []
    for i in range(n_stocks):
        sid = f"{1000 + i}"
        base_price = np.random.uniform(10, 500)
        for j in range(n_per_stock):
            rows.append({
                "stock_id": sid,
                "date": "2026-06-05",
                "price": max(1, base_price + np.random.normal(0, base_price * 0.01)),
                "volume": int(np.random.exponential(5000)),
                "change_pct": np.random.normal(0, 2),
                "name": f"Stock{sid}",
                "is_etf": i % 10 == 0,
            })
    return pd.DataFrame(rows)


class TestSmartAlertPerformance(unittest.TestCase):
    """Verify all 10 smart alert check functions complete within 50ms for 1100 stocks."""

    @classmethod
    def setUpClass(cls):
        cls.df = _make_simulated_market(1100)

    def _benchmark(self, fn, fn_name: str) -> float:
        start = time.perf_counter()
        fn(self.df)
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  {fn_name:30s} {elapsed:8.2f} ms")
        return elapsed

    def test_volume_spike(self):
        ms = self._benchmark(check_volume_spike, "check_volume_spike")
        self.assertLess(ms, 50)

    def test_high_vol_no_move(self):
        ms = self._benchmark(check_high_vol_no_move, "check_high_vol_no_move")
        self.assertLess(ms, 50)

    def test_turnover_monster(self):
        ms = self._benchmark(check_turnover_monster, "check_turnover_monster")
        self.assertLess(ms, 50)

    def test_intraday_volatility(self):
        ms = self._benchmark(check_intraday_volatility, "check_intraday_volatility")
        self.assertLess(ms, 50)

    def test_industry_momentum(self):
        ms = self._benchmark(check_industry_momentum, "check_industry_momentum")
        self.assertLess(ms, 50)

    def test_against_trend(self):
        ms = self._benchmark(check_against_trend, "check_against_trend")
        self.assertLess(ms, 50)

    def test_low_price_junk_rally(self):
        ms = self._benchmark(check_low_price_junk_rally, "check_low_price_junk_rally")
        self.assertLess(ms, 50)

    def test_etf_premium_discount(self):
        ms = self._benchmark(check_etf_premium_discount, "check_etf_premium_discount")
        self.assertLess(ms, 50)

    def test_whale_move(self):
        ms = self._benchmark(check_whale_move, "check_whale_move")
        self.assertLess(ms, 50)

    def test_active_etf_hype(self):
        ms = self._benchmark(check_active_etf_hype, "check_active_etf_hype")
        self.assertLess(ms, 50)

    def test_all_checks_combined(self):
        """Run all 10 checks sequentially and verify total < 300ms."""
        checks = [
            check_volume_spike, check_high_vol_no_move,
            check_turnover_monster, check_intraday_volatility,
            check_industry_momentum, check_against_trend,
            check_low_price_junk_rally, check_etf_premium_discount,
            check_whale_move, check_active_etf_hype,
        ]
        start = time.perf_counter()
        for fn in checks:
            fn(self.df)
        total = (time.perf_counter() - start) * 1000
        print(f"\n  {'ALL 10 CHECKS':30s} {total:8.2f} ms")
        self.assertLess(total, 300)


if __name__ == "__main__":
    unittest.main()
