from tw_quant_selector.portfolio.universe import ETF_IDS, ETF_LIST


def test_etf_list():
    assert len(ETF_LIST) == 7
    assert "0050" in ETF_IDS
    assert "006208" in ETF_IDS


def test_etf_has_all_fields():
    for e in ETF_LIST:
        assert "stock_id" in e
        assert "name" in e
