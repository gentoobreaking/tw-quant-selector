"""Tests for /api/v1/factor/* endpoints.

Reproduces T148 regression：signals.score 為 DECIMAL(8,4)，
pd.qcut → np.quantile → _lerp(Decimal * float) 會炸。
"""

from __future__ import annotations

import os

os.environ.setdefault("DUCKDB_PATH", "/tmp/test_tw_quant_factor.duckdb")

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from tw_quant_selector.api.app import app
from tw_quant_selector.data.database import Database

client = TestClient(app)
db = Database()


def setup_module():
    db.init_db()
    # 清掉既有 signals（避免與其它測試衝突）
    db.execute("DELETE FROM signals", read_only=False)
    # 模擬 DECIMAL(8,4) 欄位回傳（SQLAlchemy Decimal 行為）
    db.execute(
        "INSERT INTO signals (signal_date, stock_id, strategy, score, rank, is_selected) "
        "VALUES (CURRENT_DATE, :1, :2, :3, :4, :5)",
        ["2330", "momentum", Decimal("1.2345"), 1, True],
        read_only=False,
    )
    # 多塞幾筆讓 quintile 分組有資料
    for i, sc in enumerate(
        [Decimal("0.1"), Decimal("0.2"), Decimal("0.3"), Decimal("0.4"), Decimal("0.5"), Decimal("0.6"), Decimal("0.7"), Decimal("0.8"), Decimal("0.9")],
        start=1,
    ):
        db.execute(
            "INSERT INTO signals (signal_date, stock_id, strategy, score, rank, is_selected) "
            "VALUES (CURRENT_DATE, :1, :2, :3, :4, :5)",
            [f"99{i:02d}", "momentum", sc, i + 1, False],
            read_only=False,
        )


def teardown_module():
    db.close()
    try:
        os.remove("/tmp/test_tw_quant_factor.duckdb")
    except FileNotFoundError:
        pass


def test_quintile_returns_handles_decimal_score():
    """signals.score 回傳為 Decimal 時不應炸。"""
    resp = client.get("/api/v1/factor/quintile-returns")
    # 沒資料時可能 200 但 []，沒資料時回傳結構正常即可
    assert resp.status_code == 200, resp.text


def test_correlation_handles_decimal_score():
    """signals.score 回傳為 Decimal 時 corr 不應炸。"""
    resp = client.get("/api/v1/factor/correlation")
    assert resp.status_code == 200, resp.text