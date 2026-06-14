from __future__ import annotations
from decimal import Decimal, ROUND_UP


COMMISSION_RATE = Decimal("0.001425")
TAX_RATE_STOCK = Decimal("0.003")
TAX_RATE_ETF = Decimal("0.001")
SLIPPAGE_RATE = Decimal("0.001")
MIN_COMMISSION = Decimal("20")


def calc_buy_cost(price: Decimal, shares: int, is_etf: bool = False) -> Decimal:
    value = price * shares
    commission = max(value * COMMISSION_RATE, MIN_COMMISSION)
    slippage = value * SLIPPAGE_RATE
    return (value + commission + slippage).quantize(Decimal("0.01"), rounding=ROUND_UP)


def calc_sell_cost(price: Decimal, shares: int, is_etf: bool = False) -> Decimal:
    value = price * shares
    commission = max(value * COMMISSION_RATE, MIN_COMMISSION)
    tax = value * (TAX_RATE_ETF if is_etf else TAX_RATE_STOCK)
    slippage = value * SLIPPAGE_RATE
    return (value - commission - tax - slippage).quantize(Decimal("0.01"))
