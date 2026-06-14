import unittest
from unittest.mock import MagicMock
from datetime import date, datetime
from decimal import Decimal

from tw_quant_selector.data.realtime_valuation import (
    RealtimeValuationResult,
    compute_realtime_valuation,
    _sum_eps_ttm,
    _get_bvps,
    _get_annual_dividend,
)


class TestSumEpsTtm(unittest.TestCase):

    def test_no_financials(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        eps, ann = _sum_eps_ttm(db, "2330", date(2026, 6, 4))
        self.assertIsNone(eps)
        self.assertIsNone(ann)

    def test_four_quarters_sum(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            (10.50, "2026-06-01"),
            (11.00, "2026-03-15"),
            (10.80, "2025-12-20"),
            (10.50, "2025-09-15"),
        ]
        eps, ann = _sum_eps_ttm(db, "2330", date(2026, 6, 4))
        self.assertEqual(eps, Decimal("42.80"))
        self.assertEqual(ann, date(2026, 6, 1))

    def test_partial_quarters(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            (10.50, "2026-06-01"),
            (11.00, "2026-03-15"),
        ]
        eps, ann = _sum_eps_ttm(db, "2330", date(2026, 6, 4))
        self.assertEqual(eps, Decimal("21.50"))

    def test_some_none_eps(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            (10.50, "2026-06-01"),
            (None, "2026-03-15"),
            (10.80, "2025-12-20"),
            (10.50, "2025-09-15"),
        ]
        eps, ann = _sum_eps_ttm(db, "2330", date(2026, 6, 4))
        self.assertEqual(eps, Decimal("31.80"))

    def test_all_none_eps(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = [
            (None, "2026-06-01"),
            (None, "2026-03-15"),
        ]
        eps, ann = _sum_eps_ttm(db, "2330", date(2026, 6, 4))
        self.assertIsNone(eps)
        self.assertEqual(ann, date(2026, 6, 1))

    def test_lookahead_bias(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        eps, ann = _sum_eps_ttm(db, "2330", date(2025, 5, 1))
        self.assertIsNone(eps)
        self.assertIsNone(ann)


class TestGetBvps(unittest.TestCase):

    def test_no_financials(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [None, None]
        bvps = _get_bvps(db, "2330", date(2026, 6, 4))
        self.assertIsNone(bvps)

    def test_no_valuation(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            (100_000_000_000, 30_000_000_000, "2026Q1"),
            None,
        ]
        bvps = _get_bvps(db, "2330", date(2026, 6, 4))
        self.assertIsNone(bvps)

    def test_computes_bvps(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            (100_000_000_000, 30_000_000_000, "2026Q1"),
            (2_500_000_000_000, 943.0),
        ]
        bvps = _get_bvps(db, "2330", date(2026, 6, 4))
        expected = Decimal(70_000_000_000) / (Decimal(2_500_000_000_000) / Decimal(943))
        self.assertEqual(bvps, expected.quantize(Decimal("0.01")))

    def test_negative_equity(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            (30_000_000_000, 100_000_000_000, "2026Q1"),
            None,
        ]
        bvps = _get_bvps(db, "2330", date(2026, 6, 4))
        self.assertIsNone(bvps)

    def test_assets_none(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            (None, 30_000_000_000, "2026Q1"),
            None,
        ]
        bvps = _get_bvps(db, "2330", date(2026, 6, 4))
        self.assertIsNone(bvps)


class TestGetAnnualDividend(unittest.TestCase):

    def test_no_dividend(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [None, None]
        div = _get_annual_dividend(db, "2330", date(2026, 6, 4))
        self.assertIsNone(div)

    def test_dividend_computed(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            (0.032,),
            (943.0,),
        ]
        div = _get_annual_dividend(db, "2330", date(2026, 6, 4))
        self.assertEqual(div, Decimal("30.18"))

    def test_no_price(self):
        db = MagicMock()
        db.execute.return_value.fetchone.side_effect = [
            (0.032,),
            None,
        ]
        div = _get_annual_dividend(db, "2330", date(2026, 6, 4))
        self.assertIsNone(div)


class TestComputeRealtimeValuation(unittest.TestCase):

    def test_none_price_returns_none(self):
        db = MagicMock()
        result = compute_realtime_valuation(db, "2330", None)
        self.assertIsNone(result.current_price)
        self.assertIsNone(result.pe_rt)
        self.assertIsNone(result.pb_rt)
        self.assertIsNone(result.yield_rt)

    def test_pe_computed(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (10.50, "2026-06-01"),
                (11.00, "2026-03-15"),
                (10.80, "2025-12-20"),
                (10.50, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(
            db, "2330", 943.0, as_of_datetime=datetime(2026, 6, 4, 12, 0),
        )
        self.assertIsNotNone(result.pe_rt)
        self.assertAlmostEqual(float(result.pe_rt), 943.0 / 42.80, places=2)

    def test_negative_eps_pe_none(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (-2.00, "2026-06-01"),
                (-1.50, "2026-03-15"),
                (-0.50, "2025-12-20"),
                (-1.00, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(db, "2330", 943.0)
        self.assertIsNone(result.pe_rt)
        self.assertEqual(result.pe_detail, "虧損")

    def test_pe_over_200(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (0.50, "2026-06-01"),
                (0.60, "2026-03-15"),
                (0.55, "2025-12-20"),
                (0.45, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(db, "2330", 943.0)
        pe = 943.0 / (0.50 + 0.60 + 0.55 + 0.45)
        self.assertIsNone(result.pe_rt)
        self.assertEqual(result.pe_detail, ">200")

    def test_pb_zero(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (10.50, "2026-06-01"),
                (11.00, "2026-03-15"),
                (10.80, "2025-12-20"),
                (10.50, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(db, "2330", 943.0)
        self.assertIsNotNone(result.pb_rt)
        self.assertGreater(float(result.pb_rt), 0)

    def test_yield_computed(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (10.50, "2026-06-01"),
                (11.00, "2026-03-15"),
                (10.80, "2025-12-20"),
                (10.50, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(
            db, "2330", 943.0, as_of_datetime=datetime(2026, 6, 4, 12, 0),
        )
        self.assertIsNotNone(result.yield_rt)

    def test_ttm_eps_in_result(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (10.00, "2026-06-01"),
                (10.00, "2026-03-15"),
                (10.00, "2025-12-20"),
                (10.00, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(db, "2330", 400.0)
        self.assertEqual(result.ttm_eps, Decimal("40.00"))

    def test_bvps_in_result(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (10.50, "2026-06-01"),
                (11.00, "2026-03-15"),
                (10.80, "2025-12-20"),
                (10.50, "2025-09-15"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(db, "2330", 943.0)
        self.assertIsNotNone(result.bvps)

    def test_data_as_of(self):
        db = MagicMock()
        db.execute.side_effect = [
            MagicMock(fetchall=lambda: [
                (10.50, "2026-06-01"),
            ]),
            MagicMock(fetchone=lambda: (100_000_000_000, 30_000_000_000, "2026Q1")),
            MagicMock(fetchone=lambda: (2_500_000_000_000, 943.0)),
            MagicMock(fetchone=lambda: (0.032,)),
            MagicMock(fetchone=lambda: (943.0,)),
        ]
        result = compute_realtime_valuation(db, "2330", 100.0)
        self.assertIsNotNone(result.data_as_of)

    def test_no_financials_returns_defaults(self):
        db = MagicMock()
        db.execute.return_value.fetchall.return_value = []
        db.execute.return_value.fetchone.return_value = None
        result = compute_realtime_valuation(db, "2330", 100.0)
        self.assertIsNotNone(result.current_price)
        self.assertIsNone(result.pe_rt)
        self.assertIsNone(result.pb_rt)
        self.assertIsNone(result.yield_rt)

    def test_exception_handling(self):
        db = MagicMock()
        db.execute.side_effect = Exception("DB error")
        result = compute_realtime_valuation(db, "2330", 100.0)
        self.assertIsNotNone(result.current_price)
        self.assertIsNone(result.pe_rt)


if __name__ == "__main__":
    unittest.main()
