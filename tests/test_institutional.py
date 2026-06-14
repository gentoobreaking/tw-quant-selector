import pytest
from unittest.mock import patch, MagicMock
from tw_quant_selector.data.database import Database
from tw_quant_selector.data.ingestion import (
    update_institutional_flows_from_twse,
    update_institutional_flows_from_tpex,
    _upsert,
)


MOCK_TWSE_RESPONSE = {
    "stat": "OK",
    "date": "20260601",
    "data": [
        ["2330", "台積電", "100,000", "50,000", "50,000", "0", "0", "0",
         "20,000", "5,000", "15,000", "10,000", "8,000", "3,000", "5,000",
         "7,000", "2,000", "5,000", "75,000"],
        ["2317", "鴻海", "200,000", "180,000", "20,000", "0", "0", "0",
         "10,000", "8,000", "2,000", "5,000", "4,000", "1,000", "3,000",
         "3,000", "1,000", "2,000", "27,000"],
    ],
}


MOCK_TPEX_RESPONSE = [
    {"SecuritiesCode": "6488", "foreignBuySellDiff": 1000, "sityBuySellDiff": -500, "dealerBuySellDiff": 200},
    {"SecuritiesCode": "8069", "foreignBuySellDiff": -300, "sityBuySellDiff": 800, "dealerBuySellDiff": 0},
]


@pytest.mark.skipif(True, reason="requires live TWSE/TPEX API access and PostgreSQL")
def test_institutional_twse_fetch_and_upsert():
    db = Database()
    db.init_db()
    n = update_institutional_flows_from_twse(db, "2026-06-01")
    assert n > 0
    rows = db.execute(
        "SELECT COUNT(*) FROM institutional_flows WHERE market = 'TSE' AND trade_date = '2026-06-01'"
    ).fetchone()
    assert rows[0] > 0


def _make_mock_response(data):
    m = MagicMock()
    m.json.return_value = data
    return m


def _make_fake_client():
    fake = MagicMock()
    fake.__enter__.return_value = fake
    return fake


def test_institutional_twse_parse():
    from tw_quant_selector.data.twstock_client import fetch_twse_institutional_all

    with patch("tw_quant_selector.data.twstock_client.httpx.Client") as MockClient:
        fake_client = _make_fake_client()
        fake_client.get.return_value = _make_mock_response(MOCK_TWSE_RESPONSE)
        MockClient.return_value = fake_client

        rows = fetch_twse_institutional_all("2026-06-01")
        assert len(rows) == 2
        assert rows[0]["stock_id"] == "2330"
        assert rows[0]["trade_date"] == "2026-06-01"
        assert rows[0]["market"] == "TSE"
        assert rows[0]["foreign_investors_net"] == 50000
        assert rows[0]["sity_investors_net"] == 15000
        assert rows[0]["dealer_net"] == 10000
        assert rows[0]["dealer_proprietary_net"] == 5000
        assert rows[0]["dealer_hedge_net"] == 5000
        assert rows[0]["total_net"] == 75000


def test_institutional_tpex_parse():
    from tw_quant_selector.data.twstock_client import fetch_tpex_institutional_all

    with patch("tw_quant_selector.data.twstock_client.httpx.Client") as MockClient:
        fake_client = _make_fake_client()
        fake_client.get.return_value = _make_mock_response(MOCK_TPEX_RESPONSE)
        MockClient.return_value = fake_client

        rows = fetch_tpex_institutional_all("2026-06-01")
        assert len(rows) == 2
        assert rows[0]["stock_id"] == "6488"
        assert rows[0]["market"] == "OTC"
        assert rows[0]["foreign_investors_net"] == 1000
        assert rows[0]["sity_investors_net"] == -500


def test_institutional_tpex_graceful_fail():
    import httpx
    from tw_quant_selector.data.twstock_client import fetch_tpex_institutional_all

    with patch("tw_quant_selector.data.twstock_client.httpx.Client") as MockClient:
        fake_client = _make_fake_client()
        fake_client.get.side_effect = httpx.ConnectError("Connection refused")
        MockClient.return_value = fake_client

        rows = fetch_tpex_institutional_all("2026-06-01")
        assert rows == [], "should return empty list on failure"


def test_is_trading_day_weekend():
    from tw_quant_selector.data.twstock_client import is_trading_day

    # Saturday
    assert not is_trading_day("2026-06-06"), "Saturday should not be a trading day"
    # Sunday
    assert not is_trading_day("2026-06-07"), "Sunday should not be a trading day"


def test_fetch_with_retry_success_first_try():
    from tw_quant_selector.data.twstock_client import fetch_with_retry

    mock_fn = MagicMock(return_value=[{"stock_id": "2330"}])
    result = fetch_with_retry(mock_fn, "2026-06-01", max_retries=3, retry_delay_seconds=1)
    assert len(result) == 1
    mock_fn.assert_called_once_with("2026-06-01")


def test_fetch_with_retry_empty_then_success():
    from tw_quant_selector.data.twstock_client import fetch_with_retry

    mock_fn = MagicMock(side_effect=[
        [],
        [],
        [{"stock_id": "2330"}],
    ])
    result = fetch_with_retry(mock_fn, "2026-06-01", max_retries=3, retry_delay_seconds=1)
    assert len(result) == 1
    assert mock_fn.call_count == 3


def test_fetch_with_retry_exhausted():
    from tw_quant_selector.data.twstock_client import fetch_with_retry

    mock_fn = MagicMock(return_value=[])
    result = fetch_with_retry(mock_fn, "2026-06-01", max_retries=3, retry_delay_seconds=1)
    assert result == []
    assert mock_fn.call_count == 3
