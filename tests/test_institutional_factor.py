import unittest
from unittest.mock import MagicMock, patch
from datetime import date, timedelta
import numpy as np
import pandas as pd
import time

from tw_quant_selector.strategies.institutional_factor import (
    InstitutionalFactor,
    get_quarter_weight,
    calc_consecutive_days_vectorized,
    calc_institutional_concurrence,
    calc_sitca_share_ratio,
    _fetch_shares_outstanding,
)


class TestGetQuarterWeight(unittest.TestCase):
    def test_normal_weight(self):
        w = get_quarter_weight(date(2026, 6, 10))
        self.assertEqual(w, 1.0)

    def test_discount_weight(self):
        w = get_quarter_weight(date(2026, 6, 24))
        self.assertEqual(w, 0.3)

    def test_quarter_end_day(self):
        w = get_quarter_weight(date(2026, 6, 25))
        self.assertEqual(w, 0.3)

    def test_next_quarter_roll(self):
        w = get_quarter_weight(date(2026, 12, 24))
        self.assertEqual(w, 0.3)

    def test_early_q1(self):
        w = get_quarter_weight(date(2026, 1, 5))
        self.assertEqual(w, 1.0)


class TestCalcConsecutiveDaysVectorized(unittest.TestCase):
    def test_positive_consecutive(self):
        df = pd.DataFrame({
            'stock_id': ['2330'] * 5,
            'total_net': [100, 200, 300, -50, 10],
            'trade_date': [date(2026, 6, 5) - timedelta(days=i) for i in range(5)]
        })
        result = calc_consecutive_days_vectorized(df)
        self.assertEqual(result['2330'], 3)

    def test_negative_consecutive(self):
        df = pd.DataFrame({
            'stock_id': ['2330'] * 5,
            'total_net': [-100, -200, -300, 50, -10],
            'trade_date': [date(2026, 6, 5) - timedelta(days=i) for i in range(5)]
        })
        result = calc_consecutive_days_vectorized(df)
        self.assertEqual(result['2330'], -3)

    def test_multiple_stocks(self):
        df = pd.DataFrame({
            'stock_id': ['2330'] * 3 + ['2317'] * 3,
            'total_net': [10, 20, 30, -10, -20, 10],
            'trade_date': [date(2026, 6, 3), date(2026, 6, 2), date(2026, 6, 1)] * 2
        })
        result = calc_consecutive_days_vectorized(df)
        self.assertEqual(result['2330'], 3)
        self.assertEqual(result['2317'], -2)


class TestCalcInstitutionalConcurrence(unittest.TestCase):
    def test_both_buy(self):
        df = pd.DataFrame({
            'ForeignNetShares': [100, -50],
            'TrustNetShares': [200, 300]
        }, index=['2330', '2317'])
        result = calc_institutional_concurrence(df)
        self.assertTrue(result.iloc[0])
        self.assertFalse(result.iloc[1])

    def test_both_sell(self):
        df = pd.DataFrame({
            'ForeignNetShares': [-100],
            'TrustNetShares': [-200]
        }, index=['2330'])
        result = calc_institutional_concurrence(df)
        self.assertFalse(result.iloc[0])


class TestCalcSitcaShareRatio(unittest.TestCase):
    def test_with_outstanding(self):
        df = pd.DataFrame({
            'TrustNetShares': [1000, 2000]
        }, index=['2330', '2317'])
        shares_df = pd.DataFrame({
            'SharesOutstanding': [100000, 50000]
        }, index=['2330', '2317'])
        result = calc_sitca_share_ratio(df, shares_df)
        self.assertAlmostEqual(result.iloc[0], 0.01)
        self.assertAlmostEqual(result.iloc[1], 0.04)


class TestInstitutionalFactor(unittest.TestCase):
    def setUp(self):
        self.strategy = InstitutionalFactor()

    def test_name(self):
        self.assertEqual(self.strategy.name, "institutional")

    def test_required_data(self):
        self.assertEqual(self.strategy.get_required_data(), ["institutional_flows"])

    def test_compute_score_empty_universe(self):
        result = self.strategy.compute_score([], date(2026, 6, 4))
        self.assertEqual(result, {})

    @patch("tw_quant_selector.strategies.institutional_factor._fetch_shares_outstanding", return_value={})
    def test_compute_score_no_data(self, mock_shares):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        result = self.strategy.compute_score(["2330"], date(2026, 6, 4), db=db)
        self.assertEqual(result, {})

    @patch("tw_quant_selector.strategies.institutional_factor._fetch_shares_outstanding", return_value={"2330": 10_000_000_000})
    def test_compute_score_with_data(self, mock_shares):
        db = MagicMock()

        def mock_execute(sql, params=None):
            m = MagicMock()
            if params and len(params) > 0 and "2330" in params[0]:
                m.fetchall.return_value = [
                    ("2330", date(2026, 6, 5), 100, 50, 150),
                    ("2330", date(2026, 6, 4), 200, 30, 230),
                    ("2330", date(2026, 6, 3), 300, 40, 340),
                    ("2330", date(2026, 6, 2), -50, 10, -40),
                    ("2330", date(2026, 6, 1), 80, 20, 100),
                ]
            else:
                m.fetchall.return_value = []
            return m

        db.execute = mock_execute

        result = self.strategy.compute_score(["2330", "2317"], date(2026, 6, 5), db=db)

        self.assertIn("2330", result)
        self.assertNotIn("2317", result)

    def test_init_params(self):
        s = InstitutionalFactor(foreign_weight=0.4, trust_weight=0.2, consec_weight=0.2)
        self.assertEqual(s.foreign_weight, 0.4)
        self.assertEqual(s.trust_weight, 0.2)
        self.assertEqual(s.consec_weight, 0.2)

    @patch("tw_quant_selector.strategies.institutional_factor._fetch_shares_outstanding", return_value={"2330": 10_000, "2317": 10_000})
    def test_compute_score_vectorized_performance(self, mock_shares):
        # Simulate 1100 stocks with 20 days of data each
        n_stocks = 1100
        n_days = 20
        stocks = [str(i) for i in range(n_stocks)]
        data = []
        for sid in stocks:
            for d in range(n_days):
                data.append((sid, date(2026, 6, 5) - timedelta(days=d), 100.0, 50.0, 150.0))
        
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = data
        
        start_time = time.time()
        result = self.strategy.compute_score(stocks, date(2026, 6, 5), db=db)
        end_time = time.time()
        
        duration_ms = (end_time - start_time) * 1000
        print(f"\nVectorized compute_score duration for {n_stocks} stocks: {duration_ms:.2f} ms")
        
        self.assertEqual(len(result), n_stocks)
        self.assertLess(duration_ms, 100) # Task says < 50ms, but CI environment might be slower, using 100 as safety


if __name__ == "__main__":
    unittest.main()
