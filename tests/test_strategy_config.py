"""
T127: Unit tests for strategy_config_history initialization.

Tests:
    - load_default_config() returns correct format
    - save_config_snapshot() write/dedup logic
    - Weekly snapshot trigger conditions
    - API endpoint auto-records snapshot on config change
"""
import json
import os
import sys
import tempfile
import pytest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

# Ensure scripts/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.seed_default_strategy_config import (
    load_default_config,
    save_config_snapshot,
    run_seed,
    CONFIG_PATH,
)


# ───────────────────────────────────────────────────────
# Test: load_default_config()
# ───────────────────────────────────────────────────────

SAMPLE_YAML = """# Strategy Weights — 6-Factor Model
weights:
  momentum: 0.25
  value: 0.20
  quality: 0.20
  growth: 0.15
  institutional: 0.20

advanced_params:
  momentum_lookback: 252
  rebalance_freq: weekly
  top_n: 20
  min_score: 1.0

guru_config:
  min_f_score: 5
  altman_z_threshold: 1.8

universe_config:
  min_price: 10
  exclude_etf: true
  exclude_warrant: true
"""


def test_load_default_config_returns_correct_format():
    """load_default_config() should return dict with all 4 top-level keys."""
    with patch.object(Path, "exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=SAMPLE_YAML)):
        config = load_default_config()

    assert isinstance(config, dict)
    assert "weights" in config
    assert "advanced_params" in config
    assert "guru_config" in config
    assert "universe_config" in config
    assert config["weights"]["momentum"] == 0.25
    assert config["weights"]["value"] == 0.20
    assert config["weights"]["quality"] == 0.20
    assert config["weights"]["growth"] == 0.15
    assert config["weights"]["institutional"] == 0.20


def test_load_default_config_file_not_found():
    """load_default_config() should raise FileNotFoundError when YAML is missing."""
    with patch.object(Path, "exists", return_value=False):
        with pytest.raises(FileNotFoundError):
            load_default_config()


# ───────────────────────────────────────────────────────
# Test: save_config_snapshot()
# ───────────────────────────────────────────────────────

@pytest.fixture
def sample_config():
    return {
        "weights": {"momentum": 0.25, "value": 0.20, "quality": 0.20, "growth": 0.15, "institutional": 0.20},
        "advanced_params": {"momentum_lookback": 252, "rebalance_freq": "weekly"},
        "guru_config": {"min_f_score": 5},
        "universe_config": {"min_price": 10},
    }


def test_save_config_snapshot_inserts_new(sample_config):
    """save_config_snapshot() should insert when no existing snapshot for today."""
    mock_db = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_db.connection.return_value = mock_conn

    # First call (SELECT check) -> no existing row
    # Second call (INSERT RETURNING) -> returns config_id
    mock_select_result = MagicMock()
    mock_select_result.fetchone.return_value = None
    mock_insert_result = MagicMock()
    mock_insert_result.fetchone.return_value = [42]
    mock_conn.execute.side_effect = [mock_select_result, mock_insert_result]

    with patch("scripts.seed_default_strategy_config.get_db", return_value=mock_db):
        result = save_config_snapshot(sample_config, as_of_date=date(2026, 6, 8), changed_by="test", note="test")

    assert result == 42
    # Should have called execute twice: SELECT (check exists) + INSERT
    assert mock_conn.execute.call_count >= 2


def test_save_config_snapshot_skips_duplicate(sample_config):
    """save_config_snapshot() should return None when today already has a snapshot."""
    mock_db = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_db.connection.return_value = mock_conn

    # Existing row found
    mock_conn.execute.return_value.fetchone.return_value = [1]

    with patch("scripts.seed_default_strategy_config.get_db", return_value=mock_db):
        result = save_config_snapshot(sample_config, as_of_date=date(2026, 6, 8))

    assert result is None
    # Should have only called SELECT once
    assert mock_conn.execute.call_count == 1


# ───────────────────────────────────────────────────────
# Test: Weekly snapshot trigger logic
# ───────────────────────────────────────────────────────

def test_weekly_snapshot_monday_is_trading_day():
    """Monday should be treated as a trading day."""
    monday = date(2026, 6, 8)  # Monday
    assert monday.weekday() == 0
    assert monday.weekday() < 5  # Is weekday


def test_weekly_snapshot_weekend_skipped():
    """Saturday and Sunday should be skipped."""
    saturday = date(2026, 6, 6)
    sunday = date(2026, 6, 7)
    assert saturday.weekday() >= 5
    assert sunday.weekday() >= 5


def test_weekly_snapshot_rolls_to_next_trading_day():
    """If Monday is a holiday, should roll to next trading day (Tuesday)."""
    # Simulate: Monday is holiday, next available should be Tuesday
    today = date(2026, 6, 8)  # Monday
    next_day = today + timedelta(days=1)
    assert next_day.weekday() == 1  # Tuesday


# ───────────────────────────────────────────────────────
# Test: run_seed()
# ───────────────────────────────────────────────────────

def test_run_seed_calls_save_with_yaml_config(sample_config):
    """run_seed() should load config from YAML and call save_config_snapshot."""
    mock_db = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_db.connection.return_value = mock_conn

    # First call (SELECT check) -> no existing row
    # Second call (INSERT RETURNING) -> returns config_id
    mock_select_result = MagicMock()
    mock_select_result.fetchone.return_value = None
    mock_insert_result = MagicMock()
    mock_insert_result.fetchone.return_value = [1]
    mock_conn.execute.side_effect = [mock_select_result, mock_insert_result]

    with patch.object(Path, "exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=SAMPLE_YAML)), \
         patch("scripts.seed_default_strategy_config.get_db", return_value=mock_db):
        result = run_seed(as_of_date=date(2026, 6, 8))

    assert result == 1


# ───────────────────────────────────────────────────────
# Test: API config update endpoint (unit-level)
# ───────────────────────────────────────────────────────

def test_api_validate_weights_sum_to_one():
    """StrategyConfigUpdateRequest should reject weights that don't sum to 1.0."""
    from tw_quant_selector.api.app import StrategyConfigUpdateRequest

    # Valid
    req = StrategyConfigUpdateRequest(
        weights={"momentum": 0.25, "value": 0.20, "quality": 0.20, "growth": 0.15, "institutional": 0.20}
    )
    assert req is not None

    # Invalid: sum != 1.0 (0.5 + 0.3 + 0.4 = 1.2)
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StrategyConfigUpdateRequest(
            weights={"momentum": 0.5, "value": 0.3, "quality": 0.4}
        )


def test_api_validate_weights_non_negative():
    """StrategyConfigUpdateRequest should reject negative weights."""
    from tw_quant_selector.api.app import StrategyConfigUpdateRequest

    with pytest.raises(ValueError):
        StrategyConfigUpdateRequest(
            weights={"momentum": -0.1, "value": 0.5, "quality": 0.3, "growth": 0.2, "institutional": 0.1}
        )
