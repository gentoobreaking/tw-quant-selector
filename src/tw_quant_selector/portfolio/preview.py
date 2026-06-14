from __future__ import annotations
from typing import Optional, Any, Union
from datetime import date
from decimal import Decimal
from tw_quant_selector.portfolio.costs import calc_buy_cost, calc_sell_cost
from tw_quant_selector.strategies.combiner import compute_composite_scores

COMMISSION_RATE = Decimal("0.001425")
TAX_RATE_STOCK = Decimal("0.003")
TAX_RATE_ETF = Decimal("0.001")
SLIPPAGE_RATE = Decimal("0.001")


def preview_impact(db, as_of_date: date, holdings: list[dict],
                   weights: Optional[dict[str, float]] = None,
                   strategy_params: Optional[dict] = None,
                   top_n_stocks: int = 20, top_n_etfs: int = 3,
                   ) -> dict:
    result = compute_composite_scores(
        db, as_of_date, weights=weights,
        strategy_params=strategy_params,
        top_n_stocks=top_n_stocks,
        top_n_etfs=top_n_etfs,
    )
    new_sids = {s["stock_id"] for s in result.get("stocks", [])}
    new_eids = {s["stock_id"] for s in result.get("etfs", [])}
    new_all = new_sids | new_eids

    holding_ids = {h["stock_id"] for h in holdings if h.get("shares", 0) > 0}
    holding_sids = {h["stock_id"] for h in holdings if not h.get("is_etf") and h.get("shares", 0) > 0}
    holding_eids = {h["stock_id"] for h in holdings if h.get("is_etf") and h.get("shares", 0) > 0}

    to_buy = [s for s in result.get("stocks", []) if s["stock_id"] not in holding_ids]
    to_buy += [s for s in result.get("etfs", []) if s["stock_id"] not in holding_ids]
    to_sell_ids = (holding_sids - new_sids) | (holding_eids - new_eids)
    to_sell = [h for h in holdings if h["stock_id"] in to_sell_ids]
    unchanged_ids = holding_ids & new_all
    unchanged = [h for h in holdings if h["stock_id"] in unchanged_ids]

    total_sold = sum(h.get("shares", 0) for h in to_sell)
    total_held = sum(h.get("shares", 0) for h in holdings) or 1
    turnover_pct = round(total_sold / total_held * 100, 1)

    cost_info = estimate_rebalance_cost(to_buy, to_sell, holdings)

    return {
        "to_buy": to_buy,
        "to_sell": to_sell,
        "unchanged": unchanged,
        "turnover_pct": turnover_pct,
        "cost": cost_info,
    }


def estimate_rebalance_cost(to_buy: list[dict], to_sell: list[dict],
                            holdings: list[dict]) -> dict:
    buy_fees = Decimal("0")
    sell_net = Decimal("0")
    buy_count = 0
    sell_count = 0

    for item in to_buy:
        price_val = item.get("close") or item.get("price") or 0
        price = Decimal(str(abs(price_val)))
        if price <= 0:
            continue
        estimated_shares = int(round(float(100000 / max(float(price), 1) / 1000)) * 1000)
        if estimated_shares <= 0:
            estimated_shares = 1000
        is_etf = isinstance(item.get("stock_id"), str) and item["stock_id"].startswith("0")
        cost = calc_buy_cost(price, estimated_shares, is_etf)
        buy_fees += cost
        buy_count += 1

    for item in to_sell:
        price_val = item.get("close") or item.get("price") or 0
        price = Decimal(str(abs(price_val)))
        shares = int(item.get("shares", 0))
        if price <= 0 or shares <= 0:
            continue
        is_etf = isinstance(item.get("stock_id"), str) and item["stock_id"].startswith("0")
        net = calc_sell_cost(price, shares, is_etf)
        sell_net += net
        sell_count += 1

    total_cost = buy_fees + sell_net

    return {
        "buy_cost": round(float(buy_fees), 2),
        "sell_proceeds": round(float(sell_net), 2),
        "total_cost": round(float(total_cost), 2),
        "buy_count": buy_count,
        "sell_count": sell_count,
    }
