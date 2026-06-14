import unittest
from unittest.mock import MagicMock, patch
from datetime import date, datetime, time as dtime

from tw_quant_selector.data.realtime_quotes import (
    RealtimeQuote,
    MISApiClient,
    _parse_mis_quote,
    is_market_open,
    is_trading_day,
    poll_realtime,
    save_intraday_snapshot,
    close_market_prices,
    MARKET_OPEN,
    MARKET_CLOSE,
    BATCH_SIZE,
    INTERVAL_SEC,
)


class TestIsMarketOpen(unittest.TestCase):
    def test_before_open(self):
        dt = datetime(2026, 6, 4, 8, 30)
        self.assertFalse(is_market_open(dt))

    def test_during_open(self):
        dt = datetime(2026, 6, 4, 10, 0)
        self.assertTrue(is_market_open(dt))

    def test_at_close(self):
        dt = datetime(2026, 6, 4, 13, 30)
        self.assertTrue(is_market_open(dt))

    def test_after_close(self):
        dt = datetime(2026, 6, 4, 14, 0)
        self.assertFalse(is_market_open(dt))

    def test_weekend(self):
        dt = datetime(2026, 6, 6, 10, 0)
        self.assertTrue(is_market_open(dt))  # time-based, not day-based


class TestIsTradingDay(unittest.TestCase):
    def test_weekday(self):
        self.assertTrue(is_trading_day(date(2026, 6, 4)))  # Thu

    def test_saturday(self):
        self.assertFalse(is_trading_day(date(2026, 6, 6)))

    def test_sunday(self):
        self.assertFalse(is_trading_day(date(2026, 6, 7)))


class TestParseMisQuote(unittest.TestCase):
    def test_parse_normal(self):
        raw = {
            "c": "tse_2330.tw",
            "z": "895.00",
            "v": "12345",
            "tv": "67890",
            "b": "894.00_893.00",
            "a": "896.00_897.00",
            "y": "890.00",
            "tlong": "1717488000000",
        }
        q = _parse_mis_quote(raw)
        self.assertEqual(q.stock_id, "2330")
        self.assertEqual(q.price, 895.00)
        self.assertEqual(q.volume, 12345)
        self.assertEqual(q.trade_volume, 67890)
        self.assertEqual(q.bid, 894.00)
        self.assertEqual(q.ask, 896.00)
        self.assertEqual(q.change_amt, 5.00)
        self.assertEqual(q.change_pct, 0.56)

    def test_parse_otc(self):
        raw = {"c": "otc_6182.tw", "z": "45.50", "v": "5000", "tv": "5000",
               "b": "45.00", "a": "46.00", "y": "44.00", "tlong": "-"}
        q = _parse_mis_quote(raw)
        self.assertEqual(q.stock_id, "6182")
        self.assertEqual(q.price, 45.50)
        self.assertEqual(q.volume, 5000)
        self.assertEqual(q.bid, 45.00)
        self.assertEqual(q.ask, 46.00)
        self.assertEqual(q.change_amt, 1.50)
        self.assertEqual(q.change_pct, 3.41)

    def test_parse_no_trade(self):
        raw = {"c": "tse_2330.tw", "z": "-", "v": "-", "tv": "-",
               "b": "-", "a": "-", "y": "890.00", "tlong": "-"}
        q = _parse_mis_quote(raw)
        self.assertIsNone(q.price)
        self.assertIsNone(q.volume)
        self.assertIsNone(q.bid)
        self.assertIsNone(q.ask)

    def test_parse_empty_bid_ask(self):
        raw = {"c": "tse_2330.tw", "z": "100.00", "v": "1000", "tv": "1000",
               "b": "", "a": "", "y": "99.00", "tlong": "1717488000000"}
        q = _parse_mis_quote(raw)
        self.assertIsNone(q.bid)
        self.assertIsNone(q.ask)
        self.assertEqual(q.price, 100.00)


class TestMISApiClient(unittest.TestCase):
    def test_batch_size_constant(self):
        self.assertEqual(BATCH_SIZE, 50)

    def test_interval_constant(self):
        self.assertEqual(INTERVAL_SEC, 1.0)

    @patch("tw_quant_selector.data.realtime_quotes.httpx.Client")
    def test_fetch_batch_success(self, mock_client_class):
        mock_cm = MagicMock()
        mock_client_class.return_value = mock_cm
        mock_cm.__enter__.return_value = mock_cm
        mock_cm.get.return_value.json.return_value = {
            "msgArray": [
                {"c": "tse_2330.tw", "z": "895.00", "v": "1000", "tv": "1000",
                 "b": "894.00", "a": "896.00", "y": "890.00", "tlong": "1717488000000"},
            ]
        }

        client = MISApiClient()
        results = client.fetch_batch(["2330"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].stock_id, "2330")
        self.assertEqual(results[0].price, 895.00)

    @patch("tw_quant_selector.data.realtime_quotes.httpx.Client")
    def test_fetch_batch_empty(self, mock_client_class):
        mock_cm = MagicMock()
        mock_client_class.return_value = mock_cm
        mock_cm.__enter__.return_value = mock_cm
        mock_cm.get.return_value.json.return_value = {"msgArray": []}

        client = MISApiClient()
        results = client.fetch_batch(["2330"])
        self.assertEqual(results, [])

    @patch("tw_quant_selector.data.realtime_quotes.httpx.Client")
    def test_fetch_batch_exception(self, mock_client_class):
        mock_cm = MagicMock()
        mock_client_class.return_value = mock_cm
        mock_cm.__enter__.return_value = mock_cm
        mock_cm.get.side_effect = Exception("API Error")

        client = MISApiClient()
        results = client.fetch_batch(["2330"])
        self.assertEqual(results, [])

    @patch("tw_quant_selector.data.realtime_quotes.MISApiClient.fetch_batch", return_value=[])
    @patch("tw_quant_selector.data.realtime_quotes.time.sleep")
    def test_fetch_all_empty(self, mock_sleep, mock_fetch):
        client = MISApiClient()
        results = client.fetch_all([])
        self.assertEqual(results, [])
        mock_fetch.assert_not_called()

    @patch("tw_quant_selector.data.realtime_quotes.MISApiClient.fetch_batch")
    @patch("tw_quant_selector.data.realtime_quotes.time.sleep")
    def test_fetch_all_batches(self, mock_sleep, mock_fetch):
        mock_fetch.return_value = [RealtimeQuote(stock_id="2330", price=100.0)]
        client = MISApiClient(batch_size=1)
        results = client.fetch_all(["2330", "2317"])
        self.assertEqual(len(results), 2)
        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)


class TestPollFunctions(unittest.TestCase):
    @patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=False)
    def test_poll_realtime_market_closed(self, mock_open):
        result = poll_realtime(MagicMock())
        self.assertEqual(result["status"], "skipped")

    @patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=False)
    def test_save_snapshot_market_closed(self, mock_open):
        result = save_intraday_snapshot(MagicMock())
        self.assertEqual(result["status"], "skipped")

    @patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=True)
    def test_poll_realtime_empty_quotes(self, mock_open):
        db = MagicMock()
        with patch("tw_quant_selector.data.realtime_quotes.MISApiClient.fetch_all", return_value=[]):
            result = poll_realtime(db)
            self.assertEqual(result["status"], "empty")

    @patch("tw_quant_selector.data.database.get_session")
    @patch("tw_quant_selector.data.realtime_quotes.is_market_open", return_value=True)
    @patch("tw_quant_selector.data.realtime_quotes.MISApiClient.fetch_all")
    def test_poll_realtime_saves_quotes(self, mock_fetch, mock_open, mock_get_session):
        mock_fetch.return_value = [
            RealtimeQuote(stock_id="2330", price=895.0, volume=1000),
        ]
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        db = MagicMock()
        result = poll_realtime(db)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["count"], 1)
        mock_session.execute.assert_called()

    @patch("tw_quant_selector.data.realtime_quotes.MARKET_CLOSE", dtime(13, 30))
    def test_close_market_before_close(self):
        with patch("tw_quant_selector.data.realtime_quotes.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 6, 4, 12, 0)
            mock_dt.MIN = datetime.min
            result = close_market_prices(MagicMock())
            self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
