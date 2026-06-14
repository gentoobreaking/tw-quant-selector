from tw_quant_selector.data.adjustment import calc_adj_factor


def test_adj_factor_normal():
    assert calc_adj_factor(100, 90) == 100 / 90


def test_adj_factor_zero_denom():
    assert calc_adj_factor(100, 0) == 1.0


def test_adj_factor_no_change():
    assert calc_adj_factor(100, 100) == 1.0
