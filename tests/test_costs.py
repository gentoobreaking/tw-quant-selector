from decimal import Decimal
from tw_quant_selector.portfolio.costs import calc_buy_cost, calc_sell_cost


def test_buy_cost():
    cost = calc_buy_cost(Decimal("100"), 1000)
    assert cost > Decimal("100000")
    assert cost < Decimal("101000")


def test_sell_cost():
    proceeds = calc_sell_cost(Decimal("100"), 1000)
    assert proceeds < Decimal("100000")
    assert proceeds > Decimal("99000")


def test_etf_tax_rate():
    stock_proceeds = calc_sell_cost(Decimal("100"), 1000, is_etf=False)
    etf_proceeds = calc_sell_cost(Decimal("100"), 1000, is_etf=True)
    assert etf_proceeds > stock_proceeds


def test_min_commission():
    small = calc_buy_cost(Decimal("1"), 100)
    expected_value = Decimal("100")
    min_comm = Decimal("20")
    expected_total = expected_value + min_comm + expected_value * Decimal("0.001")
    assert small == expected_total.quantize(Decimal("0.01"))
