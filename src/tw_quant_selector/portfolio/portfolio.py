from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
import structlog

from tw_quant_selector.portfolio.costs import calc_buy_cost, calc_sell_cost

log = structlog.get_logger()

INITIAL_CAPITAL = Decimal("1000000")
STOCK_COUNT = 20
ETF_COUNT = 3
STOCK_WEIGHT = Decimal("0.8")
ETF_WEIGHT = Decimal("0.2")
SINGLE_HOLDING_LIMIT = Decimal("0.10")
INDUSTRY_LIMIT = Decimal("0.40")
BUFFER_STD = Decimal("0.5")


@dataclass
class Position:
    stock_id: str
    shares: int
    avg_cost: Decimal
    is_etf: bool = False

    def market_value(self, current_price: Decimal) -> Decimal:
        return current_price * self.shares


@dataclass
class Portfolio:
    initial_capital: Decimal = INITIAL_CAPITAL
    cash: Decimal = INITIAL_CAPITAL
    positions: dict[str, Position] = field(default_factory=dict)
    stock_allocation: Decimal = STOCK_WEIGHT
    etf_allocation: Decimal = ETF_WEIGHT
    max_holding: Decimal = SINGLE_HOLDING_LIMIT
    max_industry: Decimal = INDUSTRY_LIMIT

    def total_value(self, prices: dict[str, Decimal]) -> Decimal:
        mv = sum(p.market_value(prices.get(p.stock_id, Decimal("0"))) for p in self.positions.values())
        return self.cash + mv

    def rebalance(self, new_stocks: list[dict], new_etfs: list[dict],
                  prices: dict[str, Decimal], industries: dict[str, str],
                  as_of_date: date) -> list[dict[str, Any]]:
        trades: list[dict[str, Any]] = []
        new_sids = {s["stock_id"] for s in new_stocks}
        new_eids = {s["stock_id"] for s in new_etfs}
        current_sids = {sid for sid, p in self.positions.items() if not p.is_etf}
        current_eids = {sid for sid, p in self.positions.items() if p.is_etf}

        sell_sids = current_sids - new_sids
        sell_eids = current_eids - new_eids
        buy_targets = [(s["stock_id"], False) for s in new_stocks] + [(s["stock_id"], True) for s in new_etfs]

        for sid in sell_sids | sell_eids:
            pos = self.positions.pop(sid, None)
            if pos and sid in prices:
                proceeds = calc_sell_cost(prices[sid], pos.shares, pos.is_etf)
                self.cash += proceeds
                trades.append({"stock_id": sid, "action": "SELL", "shares": pos.shares,
                              "price": prices[sid], "value": proceeds, "date": as_of_date})

        total_val = self.total_value(prices)
        stock_budget = total_val * self.stock_allocation
        etf_budget = total_val * self.etf_allocation

        target_weight_s = Decimal("1") / Decimal(str(STOCK_COUNT)) if new_stocks else Decimal("0")
        target_weight_e = Decimal("1") / Decimal(str(ETF_COUNT)) if new_etfs else Decimal("0")

        for sid, is_etf in buy_targets:
            pos = self.positions.get(sid)
            target_weight = target_weight_e if is_etf else target_weight_s
            budget = total_val * target_weight

            if pos:
                pos_val = prices.get(sid, Decimal("0")) * pos.shares
                if pos_val < budget * (Decimal("1") - BUFFER_STD):
                    pass
                else:
                    trades.append({"stock_id": sid, "action": "HOLD", "shares": pos.shares,
                                  "price": prices.get(sid, Decimal("0")), "value": pos_val, "date": as_of_date})
                    continue

            price = prices.get(sid)
            if not price or price <= 0:
                continue
            shares = int(budget // (price * 1000)) * 1000 if price > 0 else 0
            if shares == 0:
                continue
            cost = calc_buy_cost(price, shares, is_etf)
            if cost > self.cash:
                shares = int(self.cash // (price * 1000)) * 1000 if price > 0 else 0
                if shares == 0:
                    continue
                cost = calc_buy_cost(price, shares, is_etf)

            self.cash -= cost
            if sid in self.positions:
                self.positions[sid].shares += shares
            else:
                self.positions[sid] = Position(stock_id=sid, shares=shares, avg_cost=price, is_etf=is_etf)
            trades.append({"stock_id": sid, "action": "BUY", "shares": shares,
                          "price": price, "value": cost, "date": as_of_date})

        log.info("portfolio.rebalanced", date=str(as_of_date), trades=len(trades), cash=float(self.cash))
        return trades
