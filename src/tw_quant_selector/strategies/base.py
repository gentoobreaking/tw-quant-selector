from __future__ import annotations
import inspect
from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Protocol, Optional
import numpy as np
import pandas as pd
from scipy.stats import zscore
from sqlalchemy import text


def safe_zscore(val, eps=1e-12):
    arr = np.asarray(val, dtype=float)
    if len(arr) <= 1:
        return np.array([0.0])
    if np.std(arr) < eps:
        return np.zeros_like(arr)
    return zscore(arr)


class DataProvider(Protocol):
    def get_daily_prices(self, stock_id: str, start: date, end: date) -> pd.DataFrame:
        ...

    def get_universe(self, as_of_date: date) -> list[str]:
        ...


class SQLAlchemyDataProvider:
    def __init__(self, db):
        self._db = db

    def get_daily_prices(self, stock_id: str, start: date, end: date) -> pd.DataFrame:
        with self._db.connection() as conn:
            return pd.DataFrame(
                conn.execute(text("""SELECT trade_date, open, high, low, close, volume, amount, adj_close
                   FROM daily_prices
                   WHERE stock_id = :stock_id AND trade_date >= :start AND trade_date <= :end
                   ORDER BY trade_date"""),
                    {"stock_id": stock_id, "start": start, "end": end}).fetchall(),
                columns=["trade_date", "open", "high", "low", "close", "volume", "amount", "adj_close"],
            )

    def get_universe(self, as_of_date: date) -> list[str]:
        from tw_quant_selector.portfolio.universe import get_universe
        result = get_universe(self._db, as_of_date)
        return [s["stock_id"] for s in result["stocks"]]


class BaseStrategy(ABC):
    name: str = "base"

    @abstractmethod
    def compute_score(self, universe: list[str], as_of_date: date) -> dict[str, float]:
        ...

    @abstractmethod
    def get_required_data(self) -> list[str]:
        ...


_strategy_registry: dict[str, type[BaseStrategy]] = {}


def register_strategy(cls: type[BaseStrategy]):
    _strategy_registry[cls.name] = cls
    return cls


def get_strategy(name: str, params: Optional[dict[str, Any]] = None) -> BaseStrategy:
    if name not in _strategy_registry:
        raise KeyError(f"Unknown strategy: {name}. Available: {list(_strategy_registry.keys())}")
    cls = _strategy_registry[name]
    if params:
        return cls(**params)
    return cls()


def list_strategies() -> list[str]:
    return list(_strategy_registry.keys())


_strategy_schemas: dict[str, dict[str, Any]] | None = None


def get_strategy_schemas() -> dict[str, dict[str, Any]]:
    global _strategy_schemas
    if _strategy_schemas is not None:
        return _strategy_schemas
    schemas: dict[str, dict[str, Any]] = {}
    for name, cls in _strategy_registry.items():
        sig = inspect.signature(cls.__init__)
        params: dict[str, Any] = {}
        annotations = getattr(cls.__init__, "__annotations__", {})
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            default = param.default
            if default is inspect.Parameter.empty:
                continue
            ann = annotations.get(pname, str)
            params[pname] = {
                "default": default,
                "type": ann.__name__ if hasattr(ann, "__name__") else str(ann),
            }
        schemas[name] = params
    _strategy_schemas = schemas
    return schemas
