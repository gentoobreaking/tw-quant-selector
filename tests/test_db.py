import os
from tw_quant_selector.data.database import Database, CREATE_TABLES_SQL


def test_init_db():
    db_path = "/tmp/test_tw_quant_ut.duckdb"
    db = Database(db_path)
    db.init_db()
    conn = db.connect()
    tables = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
    ).fetchall()
    table_names = {t[0] for t in tables}
    expected = {
        "stocks", "daily_prices", "monthly_revenue", "financials",
        "valuations", "signals", "backtest_runs", "backtest_positions",
        "backtest_equity", "alert_settings", "ingestion_tracker", "alert_log"
    }
    assert expected.issubset(table_names), f"Missing tables: {expected - table_names}"
    conn.close()
    os.remove(db_path)


def test_init_db_idempotent():
    db = Database(":memory:")
    db.init_db()
    db.init_db()
    tables = db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
    assert len(tables) == 15
