import pytest
from tw_quant_selector.data.database import Database
from tw_quant_selector.data.twse_client import update_stock_list


@pytest.mark.skipif(True, reason="requires live TWSE/TPEX API access and PostgreSQL")
def test_update_stock_list():
    db = Database()
    db.init_db()
    n = update_stock_list(db)
    assert n > 0, "should fetch some stocks"
    counts = db.execute(
        "SELECT market, COUNT(*) FROM stocks GROUP BY market"
    ).fetchall()
    markets = {m: c for m, c in counts}
    assert "TSE" in markets, "should have TSE stocks"
    assert "OTC" in markets, "should have OTC stocks"
    print(f"  TSE: {markets.get('TSE', 0)}, OTC: {markets.get('OTC', 0)}")
