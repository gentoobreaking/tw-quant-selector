"""
Seed default strategy config into strategy_config_history table.
T127: strategy_config_history 初始化

Provides:
    load_default_config()    — read config/strategy_weights_6factor.yaml
    save_config_snapshot()   — write to PostgreSQL strategy_config_history
    run_seed()               — write today's config snapshot if not exists
"""
import json
import os
from datetime import date
from pathlib import Path
from tw_quant_selector.data.database import get_db

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "strategy_weights_6factor.yaml"


def load_default_config() -> dict:
    """Read default 6-factor config from YAML file.

    Returns:
        dict with keys: weights, advanced_params, guru_config, universe_config
    """
    import yaml
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config_snapshot(
    config_dict: dict,
    as_of_date: date | None = None,
    changed_by: str = "system",
    note: str = "",
) -> int | None:
    """Write current strategy config snapshot to PostgreSQL strategy_config_history.

    Uses ON CONFLICT DO NOTHING: same-day dedup by checking changed_at::date.

    Args:
        config_dict: dict with keys weights, advanced_params, guru_config, universe_config
        as_of_date: snapshot date (default: today)
        changed_by: who triggered the change
        note: optional note for this snapshot

    Returns:
        config_id of the inserted row, or None if duplicate (today already exists)
    """
    from sqlalchemy import text
    db = get_db()
    as_of_date = as_of_date or date.today()

    with db.connection(read_only=False) as conn:
        # Check if today already has a snapshot
        existing = conn.execute(
            text(
                "SELECT 1 FROM strategy_config_history "
                "WHERE changed_at::date = :as_of_date"
            ),
            {"as_of_date": as_of_date},
        ).fetchone()

        if existing:
            print(f"Strategy config snapshot already exists for {as_of_date}, skipping")
            return None

        result = conn.execute(
            text(
                """INSERT INTO strategy_config_history
                   (weights, advanced_params, guru_config, universe_config,
                    changed_by, note)
                   VALUES (:weights, :advanced, :guru, :universe,
                           :changed_by, :note)
                   RETURNING config_id"""
            ),
            {
                "weights": json.dumps(config_dict.get("weights", {})),
                "advanced": json.dumps(config_dict.get("advanced_params", {})),
                "guru": json.dumps(config_dict.get("guru_config", {})),
                "universe": json.dumps(config_dict.get("universe_config", {})),
                "changed_by": changed_by,
                "note": note,
            },
        )
        config_id = result.fetchone()[0]
        print(f"Strategy config snapshot saved: config_id={config_id}, date={as_of_date}")
        return config_id


def run_seed(config_dict: dict | None = None, as_of_date: date | None = None):
    """Write today's default config snapshot if none exists for today.

    Args:
        config_dict: override config (default: load from YAML)
        as_of_date: snapshot date (default: today)
    """
    if config_dict is None:
        config_dict = load_default_config()

    as_of_date = as_of_date or date.today()
    return save_config_snapshot(
        config_dict, as_of_date, changed_by="seed", note="default config seed"
    )


def seed_default_strategy_config():
    """Legacy entry point for backward compatibility (used by scheduler.py)."""
    return run_seed()


if __name__ == "__main__":
    run_seed()
