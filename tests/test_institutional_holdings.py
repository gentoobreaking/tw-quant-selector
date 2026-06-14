import pytest
from unittest.mock import patch, MagicMock
from datetime import date

from tw_quant_selector.data.database import Database
from tw_quant_selector.data.ingestion import _upsert


MOCK_FINMIND_ROWS = [
    {"date": "2026-06-01", "stock_id": "2330", "ForeignInvestor": 75.5, "SITI": 2.3},
    {"date": "2026-06-01", "stock_id": "2317", "ForeignInvestor": 50.0, "SITI": 1.5},
]


def _fake_client_for(sid: str):
    m = MagicMock()
    m.get_shareholding.side_effect = lambda stock_id, start, end: [
        r for r in MOCK_FINMIND_ROWS if r["stock_id"] == stock_id
    ]
    return m


def test_fetch_holdings_parse():
    from tw_quant_selector.data.update_institutional_holdings import fetch_holdings

    client = _fake_client_for("2330")
    rows = fetch_holdings(client, "2330", "2026-06-01")
    assert len(rows) == 1, f"expected 1 row, got {len(rows)}"
    row = rows[0]
    assert row["stock_id"] == "2330"
    assert row["snapshot_date"] == "2026-06-01"
    assert row["foreign_holding_pct"] == 75.5
    assert row["trust_holding_pct"] == 2.3
    assert row["dealer_holding_pct"] == pytest.approx(22.2, rel=0.01)
    assert row["total_inst_pct"] == 100.0
    assert row["data_source"] == "finmind"
    client.get_shareholding.assert_called_once_with("2330", "2026-06-01", "2026-06-01")


def test_save_holdings():
    from tw_quant_selector.data.update_institutional_holdings import save_holdings

    rows = [
        {"stock_id": "2330", "snapshot_date": "2026-06-01",
         "foreign_holding_pct": 75.5, "trust_holding_pct": 2.3,
         "dealer_holding_pct": 22.2, "total_inst_pct": 100.0,
         "data_source": "finmind"},
    ]
    db = Database()
    n = save_holdings(db, rows)
    assert n == 1
    r = db.execute(
        "SELECT foreign_holding_pct FROM institutional_holdings WHERE stock_id = '2330' AND snapshot_date = '2026-06-01'"
    ).fetchone()
    assert r is not None
    assert float(r[0]) == 75.5


def test_save_holdings_updates_existing():
    from tw_quant_selector.data.update_institutional_holdings import save_holdings

    rows = [
        {"stock_id": "2330", "snapshot_date": "2026-06-01",
         "foreign_holding_pct": 76.0, "trust_holding_pct": 2.5,
         "dealer_holding_pct": 21.5, "total_inst_pct": 100.0,
         "data_source": "finmind"},
    ]
    db = Database()
    n = save_holdings(db, rows)
    assert n == 1
    r = db.execute(
        "SELECT foreign_holding_pct FROM institutional_holdings WHERE stock_id = '2330' AND snapshot_date = '2026-06-01'"
    ).fetchone()
    assert float(r[0]) == 76.0


@pytest.mark.skipif(True, reason="requires live FinMind API and PostgreSQL")
def test_run_holdings_update():
    from tw_quant_selector.data.update_institutional_holdings import run_holdings_update
    from tw_quant_selector.data.finmind_client import FinMindClient

    db = Database()
    client = FinMindClient()
    n = run_holdings_update(db, client, snapshot_date=date(2026, 6, 1))
    assert n > 0
    r = db.execute("SELECT COUNT(*) FROM institutional_holdings").fetchone()
    assert r[0] > 0
