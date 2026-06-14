import os
import pytest
os.environ["DUCKDB_PATH"] = "/tmp/test_tw_quant_api.duckdb"

from datetime import date
from fastapi import HTTPException
from tw_quant_selector.api.app import app
from tw_quant_selector.api.validators import validate_date_format, validate_stock_id, validate_date_range
from tw_quant_selector.data.database import Database
from fastapi.testclient import TestClient

client = TestClient(app)
db = Database()


def setup_module():
    db.init_db()


def teardown_module():
    db.close()
    if os.path.exists("/tmp/test_tw_quant_api.duckdb"):
        os.remove("/tmp/test_tw_quant_api.duckdb")


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["status"] == "ok"
    assert "meta" in body
    assert "request_id" in body["meta"]


def test_latest_signals_no_data():
    resp = client.get("/api/v1/signals/latest")
    assert resp.status_code == 200


def test_data_status():
    resp = client.get("/api/v1/data/status")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "last_price_update" in body["data"]


class TestDateValidation:
    def test_valid_date_format(self):
        result = validate_date_format("2026-05-30")
        assert result == date(2026, 5, 30)

    def test_invalid_date_format_slashes(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_format("2026/05/30")
        assert exc.value.status_code == 400
        assert "Invalid" in exc.value.detail

    def test_invalid_date_format_text(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_format("abc")
        assert exc.value.status_code == 400

    def test_invalid_date_non_existent(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_format("2026-02-30")
        assert exc.value.status_code == 400


class TestStockIdValidation:
    def test_valid_tw_stock(self):
        assert validate_stock_id("2330.TW") == "2330.TW"

    def test_valid_two_stock(self):
        assert validate_stock_id("6446.TWO") == "6446.TWO"

    def test_invalid_missing_suffix(self):
        # Bare 4-digit codes are valid per the API (regex accepts optional suffix)
        assert validate_stock_id("2330") == "2330"

    def test_invalid_non_numeric(self):
        with pytest.raises(HTTPException) as exc:
            validate_stock_id("abc.TW")
        assert exc.value.status_code == 400

    def test_invalid_wrong_suffix(self):
        with pytest.raises(HTTPException) as exc:
            validate_stock_id("2330.US")
        assert exc.value.status_code == 400


class TestDateRangeValidation:
    def test_valid_range(self):
        start, end = validate_date_range(date(2026, 1, 1), date(2026, 12, 31))
        assert start == date(2026, 1, 1)
        assert end == date(2026, 12, 31)

    def test_valid_range_default_end(self):
        start, end = validate_date_range(date(2026, 5, 30))
        assert start == date(2026, 5, 30)
        assert end is not None

    def test_start_after_end(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_range(date(2026, 12, 31), date(2026, 1, 1))
        assert exc.value.status_code == 400
        assert "cannot be after" in exc.value.detail

    def test_range_too_large(self):
        with pytest.raises(HTTPException) as exc:
            validate_date_range(date(2020, 1, 1), date(2026, 1, 1))
        assert exc.value.status_code == 400
        assert "too large" in exc.value.detail


class TestEndpointValidation:
    def test_stock_detail_invalid_format(self):
        resp = client.get("/api/v1/stock/abc")
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body

    def test_stock_detail_invalid_suffix(self):
        resp = client.get("/api/v1/stock/2330.US")
        assert resp.status_code == 422

    def test_stock_detail_bare_digits_valid(self):
        """Bare 4-digit codes pass the route regex and hit the endpoint."""
        resp = client.get("/api/v1/stock/2330")
        # If stock exists: 200; if not: 404; either way not 422
        assert resp.status_code in (200, 404)

    def test_stock_detail_valid_format_not_found(self):
        resp = client.get("/api/v1/stock/9999.TW")
        assert resp.status_code == 404

    def test_backtest_invalid_regex_date(self):
        resp = client.post("/api/v1/backtest/run", json={
            "start_date": "2026/01/01",
            "end_date": "2026-06-01"
        })
        # Pydantic pattern validation catches wrong separators -> 422
        assert resp.status_code == 422

    def test_backtest_invalid_semantic_date(self):
        resp = client.post("/api/v1/backtest/run", json={
            "start_date": "2026-13-01",
            "end_date": "2026-06-01"
        })
        # regex passes (dddd-dd-dd), but validate_date_format catches month=13 -> 400
        # regex passes (pattern matches dddd-dd-dd), but validate_date_format returns 400
        assert resp.status_code == 400

    def test_backtest_start_after_end(self):
        resp = client.post("/api/v1/backtest/run", json={
            "start_date": "2026-06-01",
            "end_date": "2026-01-01"
        })
        assert resp.status_code == 400
        body = resp.json()
        # HTTPException returns {"detail": "..."} not wrapped in api_response
        assert "detail" in body
        assert "cannot be after" in body["detail"]

    def test_backtest_range_too_large(self):
        resp = client.post("/api/v1/backtest/run", json={
            "start_date": "2020-01-01",
            "end_date": "2026-01-01"
        })
        assert resp.status_code == 400

    def test_portfolio_lot_invalid_stock_id(self):
        resp = client.post("/api/v1/portfolio", json={
            "stock_id": "2330",
            "shares": 100,
            "cost": 500.0
        })
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body

    def test_portfolio_lot_negative_shares(self):
        resp = client.post("/api/v1/portfolio", json={
            "stock_id": "2330.TW",
            "shares": -1,
            "cost": 500.0
        })
        assert resp.status_code == 422

    def test_portfolio_lot_zero_cost(self):
        resp = client.post("/api/v1/portfolio", json={
            "stock_id": "2330.TW",
            "shares": 100,
            "cost": 0.0
        })
        assert resp.status_code == 422

    def test_lot_invalid_date(self):
        resp = client.post("/api/v1/lots", json={
            "stock_id": "2330.TW",
            "date": "invalid",
            "shares": 100,
            "cost": 500.0
        })
        assert resp.status_code == 422


class TestCors:
    def test_allowed_origin(self):
        resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_disallowed_origin(self):
        resp = client.get("/health", headers={"Origin": "https://evil.com"})
        allow = resp.headers.get("access-control-allow-origin")
        assert allow is None or allow != "https://evil.com"

    def test_cors_allow_credentials(self):
        resp = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert resp.headers.get("access-control-allow-credentials") == "true"


def test_strategy_config_includes_institutional():
    resp = client.get("/api/v1/strategies/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    weights = body["data"]["default_weights"]
    assert "institutional" in weights
    assert weights["institutional"] == 0.25
    total = sum(weights.values())
    assert abs(total - 1.0) < 1e-9

    strategies = body["data"]["strategies"]
    assert "institutional" in strategies
    strat = strategies["institutional"]
    assert "params" in strat
    assert strat["params"]["foreign_weight"] == 0.5
    assert strat["params"]["trust_weight"] == 0.3
    assert strat["params"]["consec_weight"] == 0.2
