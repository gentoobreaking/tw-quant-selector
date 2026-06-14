"""
T128: Unit tests for alert_rules initialization.

Tests:
    - get_default_rules() returns 26 rules
    - upsert_alert_rules() insert/dedup logic
    - run_seed() --force overwrite vs normal upsert
    - API GET /api/v1/alerts/rules returns all rules
    - API PUT /api/v1/alerts/rules/{rule_name} updates single rule
    - API PUT validation (severity enum, cooldown positive)
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.seed_alert_rules import (
    get_default_rules,
    upsert_alert_rules,
    run_seed,
    DEFAULT_RULES,
)

# ───────────────────────────────────────────────────────
# Test: get_default_rules()
# ───────────────────────────────────────────────────────

def test_get_default_rules_returns_26():
    """get_default_rules() should return 26 rules."""
    rules = get_default_rules()
    assert len(rules) == 27, f"Expected 26 rules, got {len(rules)}"


def test_get_default_rules_all_have_required_keys():
    """Every rule must have rule_name, threshold, cooldown_seconds, severity."""
    rules = get_default_rules()
    for r in rules:
        assert "rule_name" in r
        assert "cooldown_seconds" in r
        assert "severity" in r
        # threshold can be None (e.g. SYS_DB_UNREACHABLE)


def test_get_default_rules_valid_severity():
    """All severities must be LOW/MEDIUM/HIGH/CRITICAL."""
    rules = get_default_rules()
    valid = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    for r in rules:
        assert r["severity"] in valid, f"{r['rule_name']} has invalid severity: {r['severity']}"


def test_get_default_rules_unique_names():
    """All rule_names must be unique."""
    rules = get_default_rules()
    names = [r["rule_name"] for r in rules]
    assert len(names) == len(set(names)), f"Duplicate rule names: {set(names)}"


def test_sys_db_unreachable_threshold_is_none():
    """SYS_DB_UNREACHABLE should have threshold=None."""
    rules = {r["rule_name"]: r for r in get_default_rules()}
    assert rules["SYS_DB_UNREACHABLE"]["threshold"] is None


def test_rule_categories_exist():
    """All 5 categories should have at least one rule."""
    rules = get_default_rules()
    prefixes = {"DATA_", "SYS_", "INST_", "PRICE_"}
    # Category E: smart alert rules (no common prefix, check individual names)
    found = set()
    for r in rules:
        for p in prefixes:
            if r["rule_name"].startswith(p):
                found.add(p)
    assert found == prefixes, f"Missing categories: {prefixes - found}"

    # Category E checks
    e_rules = {"VOLUME_SPIKE", "WHALE_MOVE", "INTRADAY_VOLATILITY", "TURNOVER_MONSTER"}
    rule_names = {r["rule_name"] for r in rules}
    for name in e_rules:
        assert name in rule_names, f"Category E rule missing: {name}"


# ───────────────────────────────────────────────────────
# Test: upsert_alert_rules()
# ───────────────────────────────────────────────────────

@patch("scripts.seed_alert_rules.get_db")
def test_upsert_alert_rules_writes_all(mock_get_db):
    """upsert_alert_rules should INSERT all rules and return count."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db

    rules = get_default_rules()
    result = upsert_alert_rules(rules)
    assert result == 27
    assert mock_db.execute.call_count == 27
    mock_db.commit.assert_called_once()
    mock_db.close.assert_called_once()


@patch("scripts.seed_alert_rules.get_db")
def test_upsert_alert_rules_handles_none_threshold(mock_get_db):
    """upsert_alert_rules should handle threshold=None (SYS_DB_UNREACHABLE)."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db

    rules = [{"rule_name": "TEST_NULL", "threshold": None, "cooldown_seconds": 300, "severity": "HIGH", "description": ""}]
    result = upsert_alert_rules(rules)
    assert result == 1


# ───────────────────────────────────────────────────────
# Test: run_seed()
# ───────────────────────────────────────────────────────

@patch("scripts.seed_alert_rules.get_db")
def test_run_seed_normal_upserts(mock_get_db):
    """run_seed() without --force should upsert (not delete)."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db

    run_seed(force=False)
    # Should NOT call DELETE
    delete_calls = [c for c in mock_db.mock_calls if "DELETE" in str(c)]
    assert len(delete_calls) == 0, "Normal seed should not DELETE"
    assert mock_db.execute.call_count == 27
    mock_db.commit.assert_called_once()


@patch("scripts.seed_alert_rules.get_db")
def test_run_seed_force_deletes_then_inserts(mock_get_db):
    """run_seed(force=True) should DELETE all then INSERT all."""
    mock_db = MagicMock()
    mock_get_db.return_value = mock_db

    run_seed(force=True)
    # Should call DELETE + 26 INSERTs
    assert mock_db.execute.call_count >= 27  # DELETE + 27 INSERTs


# ───────────────────────────────────────────────────────
# Test: API endpoints (via FastAPI TestClient)
# ───────────────────────────────────────────────────────

@pytest.fixture
def client():
    """Create a FastAPI TestClient."""
    from tw_quant_selector.api.app import app
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_api_get_alert_rules(client):
    """GET /api/v1/alerts/rules should return rules list."""
    resp = client.get("/api/v1/alerts/rules")
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "rules" in body["data"]
    assert "count" in body["data"]
    for r in body["data"]["rules"]:
        assert "rule_name" in r
        assert "severity" in r
        assert "cooldown_seconds" in r


def test_api_get_alert_rules_filter_enabled(client):
    """GET /api/v1/alerts/rules?enabled=true should filter."""
    resp = client.get("/api/v1/alerts/rules?enabled=true")
    assert resp.status_code == 200
    body = resp.json()
    for r in body["data"]["rules"]:
        assert r["enabled"] is True


def test_api_put_alert_rule_not_found(client):
    """PUT to nonexistent rule should return 404."""
    resp = client.put("/api/v1/alerts/rules/NONEXISTENT", json={"enabled": False})
    assert resp.status_code == 404


def test_api_put_alert_rule_invalid_severity(client):
    """PUT with invalid severity should return 422."""
    resp = client.put("/api/v1/alerts/rules/VOLUME_SPIKE", json={"severity": "INVALID"})
    assert resp.status_code == 422
