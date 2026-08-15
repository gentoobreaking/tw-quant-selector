"""T124: API tests for /api/v1/market/screen and /api/v1/smart-alerts/history.

The app module is imported with the Database class mocked so these tests
run without a PostgreSQL connection.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from tw_quant_selector.api.websocket_manager import AlertWebSocketManager


@pytest.fixture(scope="module")
def app_client():
    # Fresh import with the Database class mocked (routes use the app module's
    # global `db`), then force-replace `db` so this works even if another test
    # file already imported the app module with a real Database.
    with patch("tw_quant_selector.data.database.Database"):
        from tw_quant_selector.api import app as app_module

    db_mock = MagicMock()
    with patch.object(app_module, "db", db_mock):
        client = TestClient(app_module.app)
        yield client, app_module


@pytest.fixture
def screen_rows():
    """(stock_id, name, industry, is_etf, close, volume, prev_close)"""
    return [
        ("2330", "台積電", "半導體", False, 110.0, 50000, 100.0),   # +10.0%
        ("0050", "元大台灣50", "ETF", True, 101.5, 8000, 100.0),   # +1.5%
        ("2317", "鴻海", "電子", False, 99.0, 1000, 100.0),        # -1.0%
        ("006208", "富邦台50", "ETF", True, 100.5, 9000, 100.0),   # +0.5%
    ]


def _fetchall(app_module, rows):
    app_module.db.execute.return_value.fetchall.return_value = rows


class TestMarketScreen:
    def test_returns_all_by_default(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen")
        assert resp.status_code == 200
        stocks = {r["stock_id"] for r in resp.json()["data"]}
        assert stocks == {"2330", "0050", "2317", "006208"}

    def test_change_pct_computed(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen")
        by_id = {r["stock_id"]: r for r in resp.json()["data"]}
        assert by_id["2330"]["change_pct"] == 10.0
        assert by_id["2317"]["change_pct"] == -1.0

    def test_include_stocks_only(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen", params={"include_stocks": True, "include_etf": False})
        stocks = {r["stock_id"] for r in resp.json()["data"]}
        assert stocks == {"2330", "2317"}

    def test_include_etf_only(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen", params={"include_stocks": False, "include_etf": True})
        stocks = {r["stock_id"] for r in resp.json()["data"]}
        assert stocks == {"0050", "006208"}

    def test_volume_spike_filter(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen", params={"volume_spike": True})
        stocks = {r["stock_id"] for r in resp.json()["data"]}
        # only |change_pct| >= 3 → 2330 (+10%)
        assert stocks == {"2330"}

    def test_against_trend_filter(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen", params={"against_trend": True})
        stocks = {r["stock_id"] for r in resp.json()["data"]}
        # only change_pct <= -1 → 2317
        assert stocks == {"2317"}

    def test_limit_applied(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen", params={"limit": 2})
        assert len(resp.json()["data"]) == 2

    def test_sorted_by_abs_change_desc(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen")
        changes = [r["change_pct"] for r in resp.json()["data"]]
        assert changes == [10.0, 1.5, -1.0, 0.5]

    def test_invalid_limit_422(self, app_client, screen_rows):
        client, app_module = app_client
        _fetchall(app_module, screen_rows)

        resp = client.get("/api/v1/market/screen", params={"limit": 0})
        assert resp.status_code == 422
        resp = client.get("/api/v1/market/screen", params={"limit": 501})
        assert resp.status_code == 422


class TestSmartAlertsHistory:
    def test_returns_history_newest_first(self, app_client):
        client, app_module = app_client
        manager: AlertWebSocketManager = app_module.alert_ws_manager
        manager._history = [
            {"type": "alert_triggered", "data": {"alert_type": "WHALE_MOVE"}, "timestamp": f"t{i}"}
            for i in range(5)
        ]

        resp = client.get("/api/v1/smart-alerts/history")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 5
        assert data[0]["data"]["alert_type"] == "WHALE_MOVE"
        assert data[0]["timestamp"] == "t4"  # newest first

    def test_respects_limit_param(self, app_client):
        client, app_module = app_client
        manager: AlertWebSocketManager = app_module.alert_ws_manager
        manager._history = [{"index": i} for i in range(10)]

        resp = client.get("/api/v1/smart-alerts/history", params={"limit": 3})
        data = resp.json()["data"]
        assert [d["index"] for d in data] == [9, 8, 7]

    def test_limit_bounds(self, app_client):
        client, app_module = app_client
        manager: AlertWebSocketManager = app_module.alert_ws_manager
        manager._history = []

        assert client.get("/api/v1/smart-alerts/history", params={"limit": 0}).status_code == 422
        assert client.get("/api/v1/smart-alerts/history", params={"limit": 201}).status_code == 422
        assert client.get("/api/v1/smart-alerts/history", params={"limit": 1}).status_code == 200

    def test_empty_history(self, app_client):
        client, app_module = app_client
        app_module.alert_ws_manager._history = []

        resp = client.get("/api/v1/smart-alerts/history")
        assert resp.json()["data"] == []