from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Any, List, Optional
import asyncio
import csv
import io
import json
import os
import time
import threading
import uuid
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Query, HTTPException, Response, Body, Path as FPath, Request, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

from tw_quant_selector.data.database import Database, validate_table_name
from tw_quant_selector.strategies.base import get_strategy_schemas, list_strategies
from tw_quant_selector.strategies.combiner import compute_composite_scores, DEFAULT_5FACTOR_WEIGHTS, DEFAULT_WEIGHTS
from tw_quant_selector.backtest.engine import run_backtest
from tw_quant_selector.api.event_bus import EventBus
from tw_quant_selector.api import screener as screener_module
from tw_quant_selector.api.websocket_manager import QuoteWebSocketManager, AlertWebSocketManager
from tw_quant_selector.api.validators import validate_date_format, validate_stock_id, validate_date_range, normalize_stock_id
from tw_quant_selector.api import cagr as cagr_module
from scripts.seed_alert_rules import seed_alert_rules
import structlog

log = structlog.get_logger()

_default_origins = "http://localhost:5173,http://127.0.0.1:5173"
_allowed_origins = os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
_allowed_origins = [o.strip() for o in _allowed_origins if o.strip()]

# ── Rate Limiter (per-IP, 60 req/min for /api/* routes) ──

class _RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = self._requests[client_ip]
        # prune expired entries
        cutoff = now - self.window_seconds
        self._requests[client_ip] = [t for t in window if t > cutoff]
        if len(self._requests[client_ip]) >= self.max_requests:
            return JSONResponse(
                status_code=429,
                content={"error": {"message": "Too Many Requests", "retry_after_seconds": self.window_seconds}},
            )
        self._requests[client_ip].append(now)
        return await call_next(request)


def run_realtime_polling_task():
    """Background task to poll real-time quotes during market hours."""
    from tw_quant_selector.data.realtime_quotes import poll_realtime, save_intraday_snapshot, build_intraday_kline, is_market_open, is_trading_day
    
    polling_db = Database()
    log.info("realtime.background_task_started")
    
    last_snapshot_time = 0
    snapshot_interval = 300  # 5 minutes
    last_kline_time = 0
    kline_interval = 300  # 5 minutes (rebuilds OHLC from realtime_quotes)
    
    while True:
        try:
            now = datetime.now()
            if not is_trading_day(now.date()):
                time.sleep(3600)
                continue
                
            if not is_market_open(now):
                time.sleep(300)
                continue

            # Get stocks to poll (holdings + top picks)
            with polling_db.connection() as conn:
                h_rows = conn.execute("SELECT stock_id FROM portfolio WHERE shares > 0").fetchall()
                holdings = [r[0] for r in h_rows]
                
                s_rows = conn.execute("""
                    SELECT stock_id FROM signals 
                    WHERE strategy = 'composite' AND signal_date = (SELECT MAX(signal_date) FROM signals)
                    ORDER BY score DESC LIMIT 50
                """).fetchall()
                picks = [r[0] for r in s_rows]
                
            all_stocks = list(set(holdings + picks + ["0050", "2330", "^TWII"]))
            
            # Use the global quote_ws_manager for real-time broadcast
            async def broadcast_callback(quotes):
                await quote_ws_manager.broadcast_changed(quotes)
                
            log.info("realtime.poll_calling", stocks=len(all_stocks))
            result = poll_realtime(polling_db, all_stocks, key_stock_ids=holdings + ["0050", "2330"])
            log.info("realtime.poll_result", status=result.get("status"), count=result.get("count"))

            # Notify SSE watchers (e.g. Portfolio) that live prices changed so
            # they can refresh holdings against realtime_quotes.
            if result.get("count", 0) > 0:
                event_bus.broadcast("realtime_price_update")

            if time.time() - last_snapshot_time > snapshot_interval:
                save_intraday_snapshot(polling_db, all_stocks)
                last_snapshot_time = time.time()

            if time.time() - last_kline_time > kline_interval:
                build_intraday_kline(all_stocks)
                last_kline_time = time.time()
                
        except Exception as e:
            log.error("realtime.background_task_error", error=str(e))
            
        time.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start real-time polling in a background thread
    if os.getenv("ENABLE_REALTIME_POLLING", "true").lower() == "true":
        thread = threading.Thread(target=run_realtime_polling_task, daemon=True)
        thread.start()
        log.info("lifespan.realtime_polling_started")

    # Startup: warm CAGR cache in background
    cagr_module.warm_cache()
    log.info("lifespan.cagr_warm_started")
    screener_module.warm_cache()
    log.info("lifespan.screener_warm_started")

    yield
    # Shutdown logic (if any)
    log.info("lifespan.shutdown")

app = FastAPI(title="tw-quant-selector", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(_RateLimitMiddleware, max_requests=200, window_seconds=60)
db = Database(read_only=True)
db.init_db()
event_bus = EventBus()
quote_ws_manager = QuoteWebSocketManager()
alert_ws_manager = AlertWebSocketManager()


def api_response(data: Any, meta: Optional[dict[str, Any]] = None, error: Optional[dict[str, Any]] = None) -> dict:
    return {
        "data": data,
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_as_of": None,
            "request_id": str(uuid.uuid4()),
            **(meta or {}),
        },
        "error": error,
    }


class HealthResponse(BaseModel):
    status: str
    db_connected: bool
    last_update: Optional[str] = None


class SignalItem(BaseModel):
    stock_id: str
    name: Optional[str] = None
    score: float
    rank: int
    rank_change: Optional[int] = None
    consecutive_days: Optional[int] = None
    factor_scores: Optional[dict[str, float]] = None
    close_price: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    pe: Optional[float] = None  # T100: static PE from latest close / TTM EPS
    pb: Optional[float] = None  # T100: static PB from latest close / BVPS


class SignalResponse(BaseModel):
    date: str
    stocks: list[SignalItem]
    etfs: list[SignalItem]


class BacktestRequest(BaseModel):
    start_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: Optional[str] = Field(default=None, pattern=r'^\d{4}-\d{2}-\d{2}$')
    strategy_weights: Optional[dict[str, float]] = None
    benchmark: str = Field(default="0050", pattern=r'^[\w.-]+$')
    custom_universe: Optional[list[str]] = None


class BacktestResponse(BaseModel):
    run_id: str
    status: str


class PortfolioLotRequest(BaseModel):
    stock_id: str = Field(pattern=r'^\d{4}\.(TW|TWO)$')
    shares: int = Field(ge=1, le=1_000_000)
    cost: float = Field(ge=0.01, le=100_000.0)
    is_etf: bool = False


class LotRequest(BaseModel):
    id: Optional[str] = None
    stock_id: str = Field(pattern=r'^\d{4}\.(TW|TWO)$')
    date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    shares: int = Field(ge=1, le=1_000_000)
    cost: float = Field(ge=0.01, le=100_000.0)
    is_etf: bool = False

    @field_validator('date')
    @classmethod
    def validate_lot_date(cls, v: str) -> str:
        validate_date_format(v, 'date')
        return v


class PortfolioAlertRequest(BaseModel):
    stock_id: str
    stock_name: str = ""
    pnl: float = 0
    pnl_pct: float = 0
    threshold_type: str = "percent"
    threshold_value: float = 0
    avg_cost: float = 0
    current_price: float = 0
    shares: int = 0
    alert_enabled: bool = True


class DataStatusResponse(BaseModel):
    last_price_update: Optional[str] = None
    missing_dates: list[str] = []
    coverage: dict = {}


class AlertSettingsItem(BaseModel):
    key: str
    value: Optional[str] = None
    is_env_set: bool = False
    is_sensitive: bool = False
    has_value: bool = False  # T100: whether the sensitive field actually has a value set


class AlertRuleItem(BaseModel):
    """T128: alert rule response model."""
    rule_name: str
    enabled: bool = True
    threshold: Optional[float] = None
    cooldown_seconds: int = 3600
    severity: str = "MEDIUM"
    description: Optional[str] = None
    updated_at: Optional[str] = None
    config_json: str = "{}"
    message_template: Optional[str] = None


class AlertRuleUpdateRequest(BaseModel):
    """T128: update a single alert rule."""
    enabled: Optional[bool] = None
    threshold: Optional[float] = None
    cooldown_seconds: Optional[int] = None
    severity: Optional[str] = None
    description: Optional[str] = None
    config_json: Optional[str] = None
    message_template: Optional[str] = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            raise ValueError(f"severity must be LOW/MEDIUM/HIGH/CRITICAL, got: {v}")
        return v

    @field_validator("cooldown_seconds")
    @classmethod
    def validate_cooldown(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError(f"cooldown_seconds must be positive, got: {v}")
        return v


ALERT_KEYS = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "SMTP_SERVER", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
    "EMAIL_SENDER", "EMAIL_RECIPIENT",
    "PL_THRESHOLD", "PL_PERCENT_THRESHOLD"
]
SENSITIVE_KEYS = ["TELEGRAM_BOT_TOKEN", "SMTP_PASSWORD"]


@app.post("/api/v1/portfolio")
def add_portfolio_lot(lot: PortfolioLotRequest):
    db.execute("""
        INSERT INTO portfolio (stock_id, avg_cost, shares, is_etf)
        VALUES (?, ?, ?, ?)
    """, [lot.stock_id, lot.cost, lot.shares, lot.is_etf], read_only=False)
    event_bus.broadcast("portfolio_update")
    return api_response({"status": "success"})

@app.patch("/api/v1/portfolio/{stock_id}/thresholds")
def update_portfolio_thresholds(stock_id: str, body: dict):
    pl_thod = body.get("pl_thod")
    pl_pct_thod = body.get("pl_pct_thod")
    alert_enabled = body.get("alert_enabled")
    db.execute(
        """UPDATE portfolio SET pl_thod = ?, pl_pct_thod = ?, alert_enabled = ?
           WHERE stock_id = ?""",
        [pl_thod, pl_pct_thod, alert_enabled if alert_enabled is not None else True, stock_id],
        read_only=False,
    )
    event_bus.broadcast("portfolio_update")
    return api_response({"status": "success"})

@app.delete("/api/v1/portfolio/{stock_id}")
def delete_portfolio_stock(stock_id: str):
    db.execute("DELETE FROM portfolio WHERE stock_id = ?", [stock_id], read_only=False)
    event_bus.broadcast("portfolio_update")
    return api_response({"status": "success"})

@app.post("/api/v1/portfolio/export")
def export_portfolio_endpoint():
    """Export current holdings DB -> .stock_monitor.json + stock_monitor.csv.

    Delegates to scripts/export_portfolio.export_portfolio() (uses its own
    write-enabled Database session)."""
    import json as _json
    from pathlib import Path
    try:
        from scripts.export_portfolio import export_portfolio
        export_portfolio()
        # export_portfolio writes .stock_monitor.json at repo root
        root = Path.cwd()
        jp = root / ".stock_monitor.json"
        exported = 0
        if jp.exists():
            exported = len(_json.loads(jp.read_text(encoding="utf-8")))
        event_bus.broadcast("portfolio_update")
        return api_response({"status": "success", "exported": exported})
    except Exception as e:
        log.error("portfolio_export_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/portfolio/import")
def import_portfolio_endpoint(file: UploadFile = File(...)):
    """Import holdings from an uploaded .csv or .json, upserting into the DB.

    CSV → delegates to scripts/sync_portfolio_csv.convert_csv_to_json()
    (writes DB + a temp JSON). JSON → parsed and upserted via the app db."""
    import tempfile, json, csv as _csv
    from pathlib import Path

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".json"):
        raise HTTPException(400, "file must be .csv or .json")

    raw = file.file.read()
    tmp_out = Path(tempfile.mkdtemp()) / ".stock_monitor.json"

    try:
        if suffix == ".csv":
            # Write the uploaded CSV to a temp file and reuse the existing importer
            tmp_csv = Path(tempfile.mkstemp(suffix=".csv")[1])
            tmp_csv.write_bytes(raw)
            from scripts.sync_portfolio_csv import convert_csv_to_json
            convert_csv_to_json(str(tmp_csv), str(tmp_out))
            holdings = json.loads(tmp_out.read_text(encoding="utf-8")) if tmp_out.exists() else []
        else:
            holdings = json.loads(raw.decode("utf-8"))
            # Persist JSON mirror alongside DB update
            tmp_out.parent.mkdir(parents=True, exist_ok=True)
            tmp_out.write_text(json.dumps(holdings, indent=2, ensure_ascii=False), encoding="utf-8")
            # Upsert each holding via the app db (write-enabled per-statement)
            for h in holdings:
                sid = h["stock_id"]
                avg_cost = float(h.get("avg_cost", 0))
                shares = int(h.get("shares", 0))
                is_etf = bool(h.get("is_etf", False))
                pl_pct = h.get("pl_pct_thod")
                pl_thod = h.get("pl_thod")
                alert_enabled = h.get("alert_enabled", True) if h.get("alert_enabled") is not None else True
                db.execute("""
                    INSERT INTO portfolio (stock_id, avg_cost, shares, is_etf, pl_pct_thod, pl_thod, alert_enabled)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (stock_id) DO UPDATE SET
                        avg_cost = EXCLUDED.avg_cost,
                        shares = EXCLUDED.shares,
                        is_etf = EXCLUDED.is_etf,
                        pl_pct_thod = EXCLUDED.pl_pct_thod,
                        pl_thod = EXCLUDED.pl_thod,
                        alert_enabled = EXCLUDED.alert_enabled,
                        updated_at = CURRENT_TIMESTAMP
                """, [sid, avg_cost, shares, is_etf, pl_pct, pl_thod, alert_enabled], read_only=False)

        event_bus.broadcast("portfolio_update")
        return api_response({"status": "success", "imported": len(holdings)})
    except HTTPException:
        raise
    except Exception as e:
        log.error("portfolio_import_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/lots")
def get_lots():
    rows = db.execute("SELECT id, stock_id, date, shares, cost FROM lots ORDER BY date").fetchall()
    return api_response([{"id": r[0], "stock_id": r[1], "date": str(r[2]), "shares": int(r[3]), "cost": float(r[4])} for r in rows])

@app.post("/api/v1/lots")
def add_lot(body: LotRequest):
    lid = body.id or str(uuid.uuid4())
    db.execute("INSERT INTO lots (id, stock_id, date, shares, cost) VALUES (?, ?, ?, ?, ?)",
               [lid, body.stock_id, body.date, body.shares, body.cost], read_only=False)
    # Upsert portfolio aggregate
    existing = db.execute("SELECT avg_cost, shares FROM portfolio WHERE stock_id = ?", [body.stock_id]).fetchone()
    if existing:
        old_shares = int(existing[1])
        old_cost = float(existing[0])
        new_shares = body.shares
        new_cost = body.cost
        total_shares = old_shares + new_shares
        avg_cost = (old_cost * old_shares + new_cost * new_shares) / total_shares
        db.execute("UPDATE portfolio SET shares = ?, avg_cost = ? WHERE stock_id = ?",
                   [total_shares, avg_cost, body.stock_id], read_only=False)
    else:
        db.execute("INSERT INTO portfolio (stock_id, avg_cost, shares, is_etf) VALUES (?, ?, ?, ?)",
                   [body.stock_id, body.cost, body.shares, body.is_etf], read_only=False)
    event_bus.broadcast("portfolio_update")
    return api_response({"status": "success", "id": lid})

@app.delete("/api/v1/lots/{lot_id}")
def delete_lot(lot_id: str):
    row = db.execute("SELECT stock_id, shares, cost FROM lots WHERE id = ?", [lot_id]).fetchone()
    if row:
        sid = row[0]
        del_shares = int(row[1])
        del_cost = float(row[2])
        db.execute("DELETE FROM lots WHERE id = ?", [lot_id], read_only=False)
        # Recalculate portfolio from remaining lots
        remaining = db.execute("SELECT SUM(shares), AVG(cost) FROM lots WHERE stock_id = ?", [sid]).fetchone()
        if remaining and remaining[0]:
            db.execute("UPDATE portfolio SET shares = ?, avg_cost = ? WHERE stock_id = ?",
                       [int(remaining[0]), float(remaining[1]), sid], read_only=False)
        else:
            # Revert portfolio: subtract the deleted lot
            existing = db.execute("SELECT shares, avg_cost FROM portfolio WHERE stock_id = ?", [sid]).fetchone()
            if existing:
                old_shares = int(existing[0])
                old_avg = float(existing[1])
                new_shares = old_shares - del_shares
                if new_shares > 0:
                    new_avg = (old_avg * old_shares - del_cost * del_shares) / new_shares
                    db.execute("UPDATE portfolio SET shares = ?, avg_cost = ? WHERE stock_id = ?",
                               [new_shares, new_avg, sid], read_only=False)
                else:
                    db.execute("DELETE FROM portfolio WHERE stock_id = ?", [sid], read_only=False)
        event_bus.broadcast("portfolio_update")
    return api_response({"status": "success"})

@app.get("/api/v1/portfolio/events")
async def portfolio_events():
    q = event_bus.subscribe()
    # PostgreSQL：用 portfolio 表最新 updated_at 判断变化
    try:
        row = db.execute("SELECT MAX(updated_at) FROM portfolio").fetchone()
        last_update = row[0] if row and row[0] else 0.0
    except Exception:
        last_update = 0.0

    async def event_generator():
        nonlocal last_update
        try:
            heartbeat_interval = 30
            last_heartbeat = time.monotonic()
            while True:
                now = time.monotonic()
                if now - last_heartbeat >= heartbeat_interval:
                    yield ": heartbeat\n\n"
                    last_heartbeat = now
                
                # Check for portfolio table changes (PostgreSQL)
                try:
                    row = db.execute("SELECT MAX(updated_at) FROM portfolio").fetchone()
                    current_update = row[0] if row and row[0] else 0.0
                    if current_update > last_update:
                        last_update = current_update
                        yield f"data: {json.dumps({'type': 'portfolio_update'})}\n\n"
                except Exception:
                    pass

                try:
                    payload = q.get_nowait()
                    yield f"data: {payload}\n\n"
                except:
                    await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/v1/portfolio")
def get_portfolio():
    rows = db.execute("""
        SELECT p.stock_id, p.avg_cost, p.shares, p.is_etf, s.market,
               p.pl_pct_thod, p.pl_thod, p.alert_enabled,
               s.stock_name
        FROM portfolio p
        LEFT JOIN stocks s ON p.stock_id = s.stock_id
    """).fetchall()
    
    portfolio = []
    for r in rows:
        portfolio.append({
            "stock_id": r[0],
            "avgCost": float(r[1]),
            "totalShares": int(r[2]),

            "is_etf": bool(r[3]),
            "market": (r[4] or "TSE").upper(),
            "pl_pct_thod": float(r[5]) if r[5] is not None else None,
            "pl_thod": float(r[6]) if r[6] is not None else None,
            "alert_enabled": bool(r[7]) if r[7] is not None else True,
            "name": str(r[8]) if r[8] else ""
        })
    return api_response(portfolio)

@app.get("/api/v1/alerts/log")
def get_alert_log(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(30, ge=1, le=100, description="Items per page (5/10/20/30/40/50)")
):
    # Validate page_size
    allowed_sizes = [5, 10, 20, 30, 40, 50]
    if page_size not in allowed_sizes:
        page_size = 30  # default
    
    offset = (page - 1) * page_size
    
    # Get total count
    total = db.execute("SELECT COUNT(*) FROM alert_log").fetchone()[0]
    total_pages = (total + page_size - 1) // page_size
    
    # Get paginated data
    rows = db.execute("""
        SELECT log_id, stock_id, triggered_at, pnl, pnl_pct, threshold_type, threshold_value,
               avg_cost, current_price, shares, sent, reason
        FROM alert_log
        ORDER BY triggered_at DESC
        LIMIT ? OFFSET ?
    """, [page_size, offset]).fetchall()
    
    items = [{
        "log_id": r[0],
        "stock_id": r[1],
        "triggered_at": str(r[2]) if r[2] else None,
        "pnl": float(r[3]) if r[3] is not None else None,
        "pnl_pct": float(r[4]) if r[4] is not None else None,
        "threshold_type": r[5],
        "threshold_value": float(r[6]) if r[6] is not None else None,
        "avg_cost": float(r[7]) if r[7] is not None else None,
        "current_price": float(r[8]) if r[8] is not None else None,
        "shares": int(r[9]) if r[9] is not None else None,
        "sent": bool(r[10]) if r[10] is not None else False,
        "reason": r[11],
    } for r in rows]
    
    return api_response({
        "items": items,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        }
    })


class BatchDeleteAlertLogRequest(BaseModel):
    log_ids: List[str] = Field(..., description="List of log_ids to delete")


@app.delete("/api/v1/alerts/log/batch")
def delete_alert_log_batch(req: BatchDeleteAlertLogRequest):
    """Batch delete multiple alert log entries."""
    log_ids = req.log_ids
    if not log_ids:
        raise HTTPException(400, "log_ids cannot be empty")

    deleted = []
    not_found = []

    with db.connection(read_only=False) as conn:
        for log_id in log_ids:
            row = conn.execute("SELECT 1 FROM alert_log WHERE log_id = ?", [log_id]).fetchone()
            if not row:
                not_found.append(log_id)
                continue
            conn.execute("DELETE FROM alert_log WHERE log_id = ?", [log_id])
            deleted.append(log_id)
        conn.commit()

    log.info("alert_log_deleted_batch", count=len(deleted), not_found=len(not_found))
    return api_response({
        "deleted": deleted,
        "not_found": not_found,
        "total_deleted": len(deleted),
    })


@app.delete("/api/v1/alerts/log/{log_id}")
def delete_alert_log(log_id: str = FPath(..., description="alert log id")):
    """Delete a single alert log entry."""
    with db.connection(read_only=False) as conn:
        row = conn.execute("SELECT 1 FROM alert_log WHERE log_id = ?", [log_id]).fetchone()
        if not row:
            raise HTTPException(404, f"Alert log {log_id} not found")
        conn.execute("DELETE FROM alert_log WHERE log_id = ?", [log_id])
        conn.commit()
    log.info("alert_log_deleted", log_id=log_id)
    return api_response({"deleted": log_id})

@app.get("/api/v1/alerts/rules")
def get_alert_rules(enabled: Optional[bool] = Query(None, description="filter by enabled status")):
    """T128: read all alert rules from alert_rules table."""
    # Auto-seed if rules table is empty (first-time access)
    has_rules = db.execute("SELECT 1 FROM alert_rules LIMIT 1").fetchone()
    if not has_rules:
        seed_alert_rules()
    query = "SELECT rule_name, enabled, threshold, cooldown_seconds, severity, description, updated_at, config_json, message_template FROM alert_rules"
    if enabled is not None:
        query += f" WHERE enabled = {str(enabled).upper()}"
    query += " ORDER BY severity DESC, rule_name ASC"
    rows = db.execute(query).fetchall()
    rules = [
        AlertRuleItem(
            rule_name=r[0],
            enabled=r[1],
            threshold=r[2],
            cooldown_seconds=r[3],
            severity=r[4],
            description=r[5],
            updated_at=r[6].isoformat() if r[6] else None,
            config_json=r[7] or "{}",
            message_template=r[8],
        )
        for r in rows
    ]
    return api_response({"rules": rules, "count": len(rules)})

@app.put("/api/v1/alerts/rules/{rule_name}")
def update_alert_rule(rule_name: str, body: AlertRuleUpdateRequest):
    """T128: update a single alert rule (enabled, threshold, cooldown_seconds, severity, description)."""
    with db.connection(read_only=False) as conn:
        row = conn.execute("SELECT 1 FROM alert_rules WHERE rule_name = ?", [rule_name]).fetchone()
        if not row:
            raise HTTPException(404, f"Alert rule '{rule_name}' not found")

        sets = []
        params = []
        if body.enabled is not None:
            sets.append("enabled = ?")
            params.append(body.enabled)
        if body.threshold is not None:
            sets.append("threshold = ?")
            params.append(body.threshold)
        if body.cooldown_seconds is not None:
            sets.append("cooldown_seconds = ?")
            params.append(body.cooldown_seconds)
        if body.severity is not None:
            sets.append("severity = ?")
            params.append(body.severity)
        if body.description is not None:
            sets.append("description = ?")
            params.append(body.description)
        if body.config_json is not None:
            sets.append("config_json = ?")
            params.append(body.config_json)
        if body.message_template is not None:
            sets.append("message_template = ?")
            params.append(body.message_template)

        if sets:
            sets.append("updated_at = CURRENT_TIMESTAMP")
            params.append(rule_name)
            conn.execute(f"UPDATE alert_rules SET {', '.join(sets)} WHERE rule_name = ?", params)
            conn.commit()
            log.info("alert_rule_updated", rule_name=rule_name)

    # Return updated rule
    row = db.execute(
        "SELECT rule_name, enabled, threshold, cooldown_seconds, severity, description, updated_at, config_json, message_template FROM alert_rules WHERE rule_name = ?",
        [rule_name]
    ).fetchone()
    return api_response(AlertRuleItem(
        rule_name=row[0], enabled=row[1], threshold=row[2],
        cooldown_seconds=row[3], severity=row[4], description=row[5],
        updated_at=row[6].isoformat() if row[6] else None,
        config_json=row[7] or "{}",
        message_template=row[8],
    ))

@app.get("/api/v1/settings/alerts")
def get_alert_settings():
    """T100: sensitive fields return '***' + has_value flag, never the real value."""
    db_settings = {r[0]: r[1] for r in db.execute("SELECT key, value FROM alert_settings").fetchall()}
    results = []
    for k in ALERT_KEYS:
        env_val = os.getenv(k)
        is_env = env_val is not None
        val = env_val if is_env else db_settings.get(k)
        is_sensitive = k in SENSITIVE_KEYS
        has_value = bool(val)

        display_val = val
        if is_sensitive and val:
            display_val = "***"

        results.append(AlertSettingsItem(
            key=k,
            value=display_val,
            is_env_set=is_env,
            is_sensitive=is_sensitive,
            has_value=has_value,
        ))
    return api_response(results)


@app.post("/api/v1/settings/alerts")
def update_alert_settings(settings: dict[str, str]):
    """T100: skip '***' values — prevents overwriting sensitive fields with the mask."""
    with db.connection(read_only=False) as conn:
        for k, v in settings.items():
            if k not in ALERT_KEYS:
                continue
            # Skip if set by env
            if os.getenv(k) is not None:
                continue
            # T100: skip masked values — indicates user did not edit the field
            if k in SENSITIVE_KEYS and v == "***":
                continue

            is_sensitive = k in SENSITIVE_KEYS
            conn.execute(
                "INSERT INTO alert_settings (key, value, is_sensitive, updated_at) VALUES (?, ?, ?, now()) "
                "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = now()",
                [k, v, is_sensitive]
            )
        conn.commit()
    return api_response({"status": "updated"})


@app.post("/api/v1/settings/test-alert")
def test_alert():
    from tw_quant_selector.monitoring.alerting import AlertManager
    manager = AlertManager(db)
    try:
        manager.send_notification(
            "[tw-quant-selector] 測試告警",
            "這是一封測試告警郵件/訊息，如果您收到此訊息，表示設定正確。"
        )
        return api_response({"status": "sent"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/portfolio/alert")
def portfolio_alert(req: PortfolioAlertRequest):
    from tw_quant_selector.monitoring.alerting import AlertManager
    manager = AlertManager(db)
    result = manager.handle_pl_alert(req.model_dump())
    return api_response(result)


@app.post("/api/v1/notify-realtime-update")
def notify_realtime_update():
    """Trigger SSE event for realtime price update."""
    try:
        event_bus.broadcast("realtime_price_update")
        return api_response({"status": "ok", "message": "Realtime price update event sent"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.websocket("/ws/quotes")
async def ws_quotes(ws: WebSocket):
    await quote_ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        quote_ws_manager.disconnect(ws)
    except Exception:
        quote_ws_manager.disconnect(ws)


@app.websocket("/ws/alerts")
async def ws_alerts(ws: WebSocket):
    await alert_ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        alert_ws_manager.disconnect(ws)
    except Exception:
        alert_ws_manager.disconnect(ws)


@app.post("/api/v1/notify-smart-alert")
async def notify_smart_alert(payload: dict[str, Any]):
    """Broadcast a smart alert to all connected WebSocket clients."""
    await alert_ws_manager.broadcast_alert(payload)
    return api_response({"status": "ok", "broadcast": True})


@app.get("/api/v1/smart-alerts/history")
def smart_alerts_history(limit: int = Query(50, ge=1, le=200)):
    """Return recent smart alerts from in-memory store."""
    return api_response(alert_ws_manager.get_recent(limit))


@app.get("/api/v1/market/screen")
def market_screen(
    include_stocks: bool = Query(True),
    include_etf: bool = Query(True),
    volume_spike: bool = Query(False),
    against_trend: bool = Query(False),
    limit: int = Query(200, ge=1, le=500),
):
    """Market-wide stock screener. Returns stocks with price, change, volume, industry."""
    rows = db.execute("""
        WITH latest AS (
            SELECT stock_id, close AS close, volume AS volume,
                   trade_date AS price_date,
                   LAG(close) OVER (PARTITION BY stock_id ORDER BY trade_date) AS prev_close
            FROM daily_prices
        )
        SELECT s.stock_id, s.stock_name, s.industry, s.is_etf,
               l.close, l.volume, l.prev_close
        FROM stocks s
        LEFT JOIN LATERAL (
            SELECT close, volume, prev_close FROM latest
            WHERE stock_id = s.stock_id AND prev_close IS NOT NULL
            ORDER BY price_date DESC LIMIT 1
        ) l ON TRUE
        WHERE 1=1
    """).fetchall()

    result = []
    for r in rows:
        stock_id, name, industry, is_etf = r[0], r[1], r[2], bool(r[3]) if r[3] is not None else False
        if not include_stocks and not is_etf:
            continue
        if not include_etf and is_etf:
            continue
        close = float(r[4]) if r[4] else None
        volume = r[5]
        prev_close = float(r[6]) if r[6] else None
        change_pct = round(((close - prev_close) / prev_close) * 100, 2) if close is not None and prev_close and prev_close != 0 else None

        if volume_spike and (change_pct is None or abs(change_pct) < 3):
            continue
        if against_trend and (change_pct is None or change_pct > -1):
            continue

        result.append({
            "stock_id": stock_id,
            "name": name,
            "industry": industry or "",
            "is_etf": is_etf,
            "close": close,
            "change_pct": change_pct,
            "volume": volume,
        })

    result.sort(key=lambda x: abs(x["change_pct"] or 0), reverse=True)
    return api_response(result[:limit])


@app.get("/api/v1/quotes/realtime")
def quotes_realtime(stocks: str = Query(..., description="Comma-separated stock IDs")):
    """REST fallback: get latest realtime quotes for given stocks."""
    stock_ids = [s.strip() for s in stocks.split(",") if s.strip()]
    if not stock_ids:
        raise HTTPException(400, "No stock IDs provided")
    placeholders = ",".join(["?"] * len(stock_ids))
    rows = db.execute(
        f"""SELECT DISTINCT ON (stock_id) stock_id, price, volume, change_pct, pe_realtime, pb_realtime, yield_realtime, quote_time
            FROM realtime_quotes
            WHERE stock_id IN ({placeholders})
            ORDER BY stock_id, quote_time DESC""",
        stock_ids,
    ).fetchall()
    result: dict[str, dict[str, Any]] = {}
    for r in rows:
        result[r[0]] = {
            "price": float(r[1]) if r[1] else None,
            "volume": r[2],
            "change_pct": float(r[3]) if r[3] else None,
            "pe_realtime": float(r[4]) if r[4] else None,
            "pb_realtime": float(r[5]) if r[5] else None,
            "yield_realtime": float(r[6]) if r[6] else None,
            "quote_time": str(r[7]) if r[7] else None,
        }
    return api_response(result)


@app.post("/api/v1/notify-websocket-update")
async def notify_websocket_update(data: Optional[dict[str, Any]] = Body(None)):
    """Trigger WebSocket broadcast with realtime quotes data."""
    try:
        if data and "quotes" in data:
            await quote_ws_manager.broadcast_changed(data["quotes"])
        else:
            await quote_ws_manager.broadcast({"type": "update"})
        return api_response({"status": "ok", "message": "WebSocket update sent"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/v1/settings/db-path")
def get_db_path():
    """PostgreSQL 版本：返回数据库连接信息（无文件路径概念）"""
    return api_response({
        "backend": "postgresql",
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": 5432,
        "database": os.getenv("POSTGRES_DB", "tw_quant"),
        "user": os.getenv("POSTGRES_USER", "tw-quant"),
        "is_env_set": os.getenv("POSTGRES_HOST") is not None
    })


@app.post("/api/v1/settings/db-path")
def update_db_path(data: dict[str, str]):
    """PostgreSQL 版本：不支持修改数据库连接（安全考虑）"""
    raise HTTPException(400, detail="PostgreSQL mode: database path cannot be changed at runtime")


@app.get("/health")
def health():
    db_ok = True
    last = None
    try:
        row = db.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
        if row and row[0]:
            last = row[0].isoformat()
    except Exception:
        db_ok = False
    return api_response(HealthResponse(status="ok", db_connected=db_ok, last_update=last).model_dump())


@app.get("/")
def root_redirect():
    return RedirectResponse(url="/docs")


@app.get("/api/v1/dashboard")
def dashboard_data():
    from datetime import date
    stats = {}
    for t in ["stocks", "daily_prices", "valuations", "monthly_revenue", "financials", "signals", "backtest_runs"]:
        n = db.execute(f"SELECT COUNT(*) FROM {validate_table_name(t)}").fetchone()
        stats[t] = n[0] if n else 0
    price_range = db.execute("SELECT MIN(trade_date), MAX(trade_date) FROM daily_prices").fetchone()
    val_range = db.execute("SELECT MIN(trade_date), MAX(trade_date) FROM valuations").fetchone()
    tracker = db.execute(
        "SELECT dataset, last_status, COUNT(*) FROM ingestion_tracker WHERE last_updated IS NOT NULL GROUP BY dataset, last_status"
    ).fetchall()
    top_volume = db.execute(
        """SELECT stock_id, COUNT(*) as days FROM daily_prices
           GROUP BY stock_id ORDER BY days DESC LIMIT 10"""
    ).fetchall()
    return api_response({
        "table_counts": stats,
        "price_date_range": {"min": str(price_range[0]) if price_range and price_range[0] else None,
                             "max": str(price_range[1]) if price_range and price_range[1] else None},
        "val_date_range": {"min": str(val_range[0]) if val_range and val_range[0] else None,
                           "max": str(val_range[1]) if val_range and val_range[1] else None},
        "tracker": [{"dataset": r[0], "status": r[1], "count": r[2]} for r in tracker],
        "top_stocks": [{"stock_id": r[0], "days": r[1]} for r in top_volume],
    })


@app.get("/api/v1/stocks/by_dataset/{dataset}")
def stocks_by_dataset(dataset: str):
    try:
        tbl = validate_table_name(dataset)
    except ValueError:
        raise HTTPException(400, f"Unknown dataset: {dataset}")
    rows = db.execute(
        f"SELECT s.stock_id, s.stock_name, s.market, COUNT(*) as cnt FROM {tbl} t JOIN stocks s ON s.stock_id = t.stock_id GROUP BY s.stock_id, s.stock_name, s.market ORDER BY cnt DESC LIMIT 100"
    ).fetchall()
    return api_response([{"stock_id": r[0], "name": r[1], "market": r[2], "count": r[3]} for r in rows])


@app.get("/api/v1/stocks/search")
def search_stocks(q: str = Query("", min_length=1)):
    like = f"%{q}%"
    rows = db.execute(
        "SELECT stock_id, stock_name, market, is_etf, industry FROM stocks WHERE stock_id LIKE ? OR stock_name LIKE ? LIMIT 20",
        [like, like]
    ).fetchall()
    return api_response([{"stock_id": r[0], "name": r[1], "market": r[2], "is_etf": bool(r[3]), "industry": r[4]} for r in rows])


@app.get("/api/v1/universe/count")
def get_universe_count(
    min_market_cap: float = Query(0, ge=0),
    min_daily_volume: float = Query(0, ge=0),
    exclude_financial: bool = Query(False),
    exclude_ky: bool = Query(False),
    include_etf: bool = Query(False),
):
    where_clauses = ["1=1"]
    params: list[Any] = []

    if not include_etf:
        where_clauses.append("s.is_etf = false")

    if exclude_financial:
        where_clauses.append("COALESCE(s.industry, '') NOT LIKE '%金融%'")
        where_clauses.append("COALESCE(s.industry, '') NOT LIKE '%保險%'")

    if exclude_ky:
        where_clauses.append("s.stock_id NOT LIKE '%KY%'")

    if min_market_cap > 0:
        where_clauses.append("mc.market_cap >= ?")
        params.append(min_market_cap)

    if min_daily_volume > 0:
        where_clauses.append("dv.avg_amount >= ?")
        params.append(min_daily_volume * 10_000_000)

    query = f"""
        SELECT COUNT(DISTINCT s.stock_id) as cnt
        FROM stocks s
        LEFT JOIN (
            SELECT stock_id, market_cap
            FROM valuations
            WHERE (stock_id, trade_date) IN (
                SELECT stock_id, MAX(trade_date)
                FROM valuations
                GROUP BY stock_id
            )
        ) mc ON mc.stock_id = s.stock_id
        LEFT JOIN (
            SELECT stock_id, AVG(amount) as avg_amount
            FROM daily_prices
            WHERE trade_date >= (SELECT DATE_TRUNC('day', MAX(trade_date) - INTERVAL '20 days') FROM daily_prices)
            GROUP BY stock_id
        ) dv ON dv.stock_id = s.stock_id
        WHERE {' AND '.join(where_clauses)}
    """

    row = db.execute(query, params).fetchone()
    count = row[0] if row else 0

    return api_response({
        "count": count,
        "filters": {
            "min_market_cap": min_market_cap,
            "min_daily_volume": min_daily_volume,
            "exclude_financial": exclude_financial,
            "exclude_ky": exclude_ky,
            "include_etf": include_etf,
        }
    })


@app.get("/api/v1/stocks/prices")
def stocks_prices(
    ids: str = Query(...),
    realtime: bool = Query(False)
):
    stock_ids = [s.strip() for s in ids.split(",") if s.strip()]
    if not stock_ids:
        raise HTTPException(400, "No stock IDs provided")
    
    if realtime:
        # Realtime quotes live in the `realtime_quotes` table, populated by the
        # background poll (poll_realtime). Prefer the latest intraday quote per
        # stock; fall back to the daily close if realtime data is unavailable.
        try:
            placeholders = ",".join([f":id{i}" for i in range(len(stock_ids))])
            params = {f"id{i}": sid for i, sid in enumerate(stock_ids)}
            rows = db.execute(f"""
                SELECT s.stock_id, s.stock_name, rq.price, rq.change_pct, rq.quote_time
                FROM stocks s
                LEFT JOIN LATERAL (
                    SELECT price, change_pct, quote_time
                    FROM realtime_quotes
                    WHERE stock_id = s.stock_id
                    ORDER BY quote_time DESC, is_close ASC LIMIT 1
                ) rq ON TRUE
                WHERE s.stock_id IN ({placeholders})
            """, params).fetchall()
            result = {}
            have_rt = False
            for r in rows:
                if r[2] is not None:
                    have_rt = True
                result[r[0]] = {
                    "name": str(r[1]) if r[1] else "",
                    "close": float(r[2]) if r[2] is not None else None,
                    "change_pct": float(r[3]) if r[3] is not None else None,
                    "date": str(r[4]) if r[4] else None,
                }
            if have_rt:
                return api_response(result)
        except Exception as e:
            log.warning("stocks_prices.realtime_failed", error=str(e))
        # Fallback: none of the realtime paths yielded prices → use daily closes

    # 原始邏輯：從 daily_prices 讀取
    placeholders = ",".join(["?"] * len(stock_ids))
    rows = db.execute(
        f"SELECT dp.stock_id, s.stock_name, dp.close, dp.trade_date FROM daily_prices dp JOIN stocks s ON s.stock_id = dp.stock_id WHERE dp.stock_id IN ({placeholders}) AND dp.trade_date = (SELECT MAX(trade_date) FROM daily_prices WHERE stock_id = dp.stock_id)",
        stock_ids
    ).fetchall()
    result = {}
    for r in rows:
        result[r[0]] = {"name": r[1], "close": float(r[2]) if r[2] is not None else None, "date": str(r[3]) if r[3] else None}
    return api_response(result)


@app.get("/api/v1/stock/{stock_id}")
def stock_detail(stock_id: str = FPath(pattern=r'^\d{4,6}[A-Z]?(\.(TW|TWO))?$')):
    sid = normalize_stock_id(stock_id)
    info = db.execute("SELECT stock_id, stock_name, market, is_etf, industry FROM stocks WHERE stock_id = ?", [sid]).fetchone()
    if not info:
        raise HTTPException(404, "Stock not found")
    prices = db.execute(
        "SELECT trade_date, open, high, low, close, volume FROM daily_prices WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 120",
        [sid]
    ).fetchall()
    vals = db.execute(
        "SELECT trade_date, pe_ratio, pb_ratio, dividend_yield FROM valuations WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 10",
        [sid]
    ).fetchall()
    fins = db.execute(
        "SELECT year_quarter, revenue, eps, roe, gross_margin, debt_to_equity FROM financials WHERE stock_id = ? ORDER BY year_quarter DESC LIMIT 8",
        [sid]
    ).fetchall()
    revs = db.execute(
        "SELECT year_month, revenue, revenue_yoy FROM monthly_revenue WHERE stock_id = ? ORDER BY year_month DESC LIMIT 12",
        [sid]
    ).fetchall()
    sig = db.execute(
        """SELECT m.score AS momentum, v.score AS value, q.score AS quality, g.score AS growth, i.score AS institutional
           FROM (SELECT COALESCE(MAX(signal_date), CURRENT_DATE) AS max_date FROM signals WHERE stock_id = ?) sd
           LEFT JOIN signals m ON m.signal_date = sd.max_date AND m.stock_id = ? AND m.strategy = 'momentum'
           LEFT JOIN signals v ON v.signal_date = sd.max_date AND v.stock_id = ? AND v.strategy = 'value'
           LEFT JOIN signals q ON q.signal_date = sd.max_date AND q.stock_id = ? AND q.strategy = 'quality'
           LEFT JOIN signals g ON g.signal_date = sd.max_date AND g.stock_id = ? AND g.strategy = 'growth'
           LEFT JOIN signals i ON i.signal_date = sd.max_date AND i.stock_id = ? AND i.strategy = 'institutional'""",
        [sid, sid, sid, sid, sid, sid]
    ).fetchone()
    factor_scores = None
    if sig:
        fs = {}
        for k, v in zip(['momentum', 'value', 'quality', 'growth', 'institutional'], sig):
            if v is not None:
                fs[k] = float(v)
        if fs:
            factor_scores = fs

    from tw_quant_selector.data.realtime_valuation import compute_realtime_valuation
    current_price = float(prices[0][4]) if prices and prices[0][4] is not None else None
    rtv = compute_realtime_valuation(db, sid, current_price)

    last_val = vals[0] if vals else None
    last_close_pe = float(last_val[1]) if last_val and last_val[1] is not None else None
    last_close_pb = float(last_val[2]) if last_val and last_val[2] is not None else None

    return api_response({
        "info": {"stock_id": info[0], "name": info[1], "market": info[2], "is_etf": info[3], "industry": info[4]},
        "prices": [{"d": str(r[0]), "o": float(r[1]) if r[1] else None, "h": float(r[2]) if r[2] else None,
                    "l": float(r[3]) if r[3] else None, "c": float(r[4]) if r[4] else None, "v": r[5]} for r in prices],
        "valuations": [{"d": str(r[0]), "pe": float(r[1]) if r[1] else None, "pb": float(r[2]) if r[2] else None,
                        "dy": float(r[3]) if r[3] else None} for r in vals],
        "financials": [{"yq": r[0], "rev": r[1], "eps": float(r[2]) if r[2] else None,
                        "roe": float(r[3]) if r[3] else None, "gm": float(r[4]) if r[4] else None,
                        "de": float(r[5]) if r[5] else None} for r in fins],
        "revenue": [{"ym": r[0], "rev": r[1], "yoy": float(r[2]) if r[2] else None} for r in revs],
        "factor_scores": factor_scores,
        "realtime_valuation": {
            "price": float(rtv.current_price) if rtv.current_price else None,
            "pe": float(rtv.pe_rt) if rtv.pe_rt else None,
            "pb": float(rtv.pb_rt) if rtv.pb_rt else None,
            "dividend_yield": float(rtv.yield_rt) if rtv.yield_rt else None,
            "ttm_eps": float(rtv.ttm_eps) if rtv.ttm_eps else None,
            "bvps": float(rtv.bvps) if rtv.bvps else None,
            "data_as_of": str(rtv.data_as_of) if rtv.data_as_of else None,
            "pe_detail": rtv.pe_detail,
            "pb_detail": rtv.pb_detail,
            "last_close_pe": last_close_pe,
            "last_close_pb": last_close_pb,
        } if current_price else None,
    })


@app.get("/api/v1/stock/{stock_id}/factor-history")
def stock_factor_history(stock_id: str, limit: int = 52):
    sid = normalize_stock_id(stock_id)
    rows = db.execute(
        """SELECT signal_date, strategy, score
           FROM signals WHERE stock_id = ? ORDER BY signal_date DESC, strategy""",
        [sid]
    ).fetchall()
    pivoted: dict[str, dict[str, Optional[float]]] = {}
    for r in rows:
        d = str(r[0])
        if d not in pivoted:
            pivoted[d] = {"date": d, "momentum": None, "value": None, "quality": None, "growth": None, "guru": None, "institutional": None}
        pivoted[d][r[1]] = float(r[2]) if r[2] is not None else None
    result = list(pivoted.values())[:limit]
    return api_response(result)


@app.get("/api/v1/monitor/status")
def monitor_status():
    """Aggregated monitor status for the dashboard."""
    # 1. System Health & Stats
    db_stats = db.execute("SELECT COUNT(*) FROM daily_prices").fetchone()
    total_prices = db_stats[0] if db_stats else 0
    
    # 2. Polling Status
    latest_snapshot = db.execute("SELECT MAX(snapshot_time) FROM intraday_snapshots").fetchone()
    is_polling_active = False
    if latest_snapshot and latest_snapshot[0]:
        is_polling_active = (datetime.now() - latest_snapshot[0].replace(tzinfo=None)).total_seconds() < 600

    # 3. Dataset Ingestion Status - Aggregate by dataset AND status
    # The frontend expects a list of objects with dataset, ok_count, total_count.
    # We query all status combinations and aggregate them in Python.
    raw_ds = db.execute(
        """SELECT dataset, last_status, COUNT(*) as cnt, MAX(last_updated) as last_upd
           FROM ingestion_tracker
           GROUP BY dataset, last_status"""
    ).fetchall()
    
    # Aggregation in memory
    agg = {}
    for r in raw_ds:
        ds = r[0]
        status = r[1]
        count = r[2]
        upd = r[3]
        if ds not in agg:
            agg[ds] = {"ok_count": 0, "total_count": 0, "last_updated": None}
        
        agg[ds]["total_count"] += count
        if status == 'ok':
            agg[ds]["ok_count"] += count
        
        if upd:
            upd_str = str(upd)
            if not agg[ds]["last_updated"] or upd_str > agg[ds]["last_updated"]:
                agg[ds]["last_updated"] = upd_str

    datasets = [{
        "dataset": k,
        "ok_count": v["ok_count"],
        "total_count": v["total_count"],
        "last_updated": v["last_updated"]
    } for k, v in agg.items()]

    return api_response({
        "system": {
            "total_prices": total_prices,
            "polling_active": is_polling_active,
            "last_active_snapshot": str(latest_snapshot[0]) if latest_snapshot and latest_snapshot[0] else None
        },
        "datasets": datasets
    })


@app.get("/api/v1/monitor/logs")
def monitor_logs():
    rows = db.execute(
        """SELECT id, module, event, severity, created_at
           FROM operation_logs WHERE created_at >= CURRENT_DATE - 7
           ORDER BY created_at DESC LIMIT 100"""
    ).fetchall()
    return api_response([{
        "id": r[0], "module": r[1], "event": r[2],
        "severity": r[3], "timestamp": str(r[4]) if r[4] else None,
    } for r in rows])


@app.get("/api/v1/signals/export.csv")
def export_signals_csv(
    date: Optional[str] = Query(None),
    strategy: str = Query("composite"),
    top_n: int = Query(200, ge=1, le=500),
):
    sd = date
    if not sd:
        row = db.execute("SELECT MAX(signal_date) FROM signals WHERE strategy = ?", [strategy]).fetchone()
        if row and row[0]:
            sd = str(row[0])
    items = []
    if sd:
        rows = db.execute(
            """SELECT s.stock_id, st.stock_name, s.score, s.rank
               FROM signals s LEFT JOIN stocks st ON s.stock_id = st.stock_id
               WHERE s.signal_date = ? AND s.strategy = ?
               ORDER BY s.rank LIMIT ?""",
            [sd, strategy, top_n]
        ).fetchall()
        for r in rows:
            items.append({"stock_id": r[0], "name": r[1] or "", "score": f"{float(r[2]):.4f}" if r[2] else "", "rank": r[3] or 0})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["rank", "stock_id", "name", "score"])
    writer.writeheader()
    writer.writerows(items)
    fname = f"tw_signals_{sd.replace('-', '')}.csv" if sd else "tw_signals.csv"
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@app.get("/api/v1/signals/export.json")
def export_signals_json(
    date: Optional[str] = Query(None),
    strategy: str = Query("composite"),
    top_n: int = Query(200, ge=1, le=500),
):
    sd = date
    if not sd:
        row = db.execute("SELECT MAX(signal_date) FROM signals WHERE strategy = ?", [strategy]).fetchone()
        if row and row[0]:
            sd = str(row[0])
    items = []
    if sd:
        rows = db.execute(
            """SELECT s.stock_id, st.stock_name, s.score, s.rank
               FROM signals s LEFT JOIN stocks st ON s.stock_id = st.stock_id
               WHERE s.signal_date = ? AND s.strategy = ?
               ORDER BY s.rank LIMIT ?""",
            [sd, strategy, top_n]
        ).fetchall()
        for r in rows:
            items.append({"stock_id": r[0], "name": r[1] or "", "score": float(r[2]) if r[2] is not None else None, "rank": r[3] or 0})
    return api_response({"signals": items, "date": sd, "strategy": strategy})


@app.get("/api/v1/backtest/{run_id}/detail")
def get_backtest_detail(run_id: str):
    row = db.execute("SELECT * FROM backtest_runs WHERE run_id = ?", [run_id]).fetchone()
    if not row:
        raise HTTPException(404, "Backtest run not found")
    trades = db.execute(
        """SELECT trade_date, stock_id, action, shares, price, value, weight
           FROM backtest_positions WHERE run_id = ? ORDER BY trade_date, stock_id""",
        [run_id]
    ).fetchall()
    total_trades = len(trades)
    return api_response({
        "run_id": run_id,
        "created_at": str(row[1]) if row[1] else None,
        "start_date": str(row[2]) if row[2] else None,
        "end_date": str(row[3]) if row[3] else None,
        "metrics": {
            "total_return": float(row[6]) if row[6] else None,
            "cagr": float(row[7]) if row[7] else None,
            "sharpe": float(row[8]) if row[8] else None,
            "max_drawdown": float(row[9]) if row[9] else None,
            "calmar": float(row[10]) if row[10] else None,
            "turnover": float(row[11]) if row[11] else None,
            "total_trades": total_trades,
        },
        "trades": [{
            "date": str(t[0]),
            "stock_id": t[1],
            "action": t[2],
            "shares": t[3] or 0,
            "price": float(t[4]) if t[4] else None,
            "value": float(t[5]) if t[5] else None,
            "weight": float(t[6]) if t[6] else None,
        } for t in trades],
    })


@app.get("/api/v1/backtest/{run_id}/equity")
def get_backtest_equity(run_id: str):
    rows = db.execute(
        "SELECT trade_date, portfolio_value, benchmark_value, drawdown FROM backtest_equity WHERE run_id = ? ORDER BY trade_date",
        [run_id]
    ).fetchall()
    return api_response([{
        "date": str(r[0]),
        "value": float(r[1]) if r[1] else None,
        "benchmark": float(r[2]) if r[2] else None,
        "drawdown": float(r[3]) if r[3] else None,
    } for r in rows])


@app.get("/api/v1/signals")
def signals_query(
    date: Optional[str] = Query(None),
    strategy: str = Query("composite"),
    top_n: int = Query(50, ge=1, le=200),
    include_etf: bool = Query(False),
):
    if date:
        from datetime import date as date_cls
        signal_date = date_cls.fromisoformat(date)
    else:
        row = db.execute("SELECT MAX(signal_date) FROM signals WHERE strategy = ?", [strategy]).fetchone()
        if not row or not row[0]:
            return api_response(SignalResponse(date="", stocks=[], etfs=[]).model_dump())
        signal_date = row[0].isoformat() if hasattr(row[0], 'isoformat') else str(row[0])
    return api_response(_get_signals(signal_date, strategy, top_n, include_etf).model_dump())


@app.get("/api/v1/valuations/latest")
def latest_valuations():
    row = db.execute("SELECT MAX(trade_date) FROM valuations").fetchone()
    if not row or not row[0]:
        return api_response([])
    max_date = row[0]
    rows = db.execute(
        """SELECT v.stock_id, v.pb_ratio, s.industry
           FROM valuations v
           JOIN stocks s ON s.stock_id = v.stock_id
           WHERE v.trade_date = ?""",
        [max_date]
    ).fetchall()
    return api_response([
        {"stock_id": r[0], "pb": float(r[1]) if r[1] else None, "industry": r[2]}
        for r in rows
    ])


@app.get("/api/v1/signals/calendar")
def signals_calendar():
    rows = db.execute(
        "SELECT DISTINCT signal_date FROM signals ORDER BY signal_date DESC LIMIT 365"
    ).fetchall()
    dates = [str(r[0]) for r in rows]
    return api_response(dates)


@app.get("/api/v1/signals/latest")
def latest_signals(
    strategy: str = Query("composite"),
    top_n: int = Query(20, ge=1, le=100),
    include_etf: bool = Query(False),
):
    latest = db.execute("SELECT MAX(signal_date) FROM signals WHERE strategy = ?", [strategy]).fetchone()
    if not latest or not latest[0]:
        return api_response(SignalResponse(date="", stocks=[], etfs=[]).model_dump())
    return api_response(_get_signals(latest[0], strategy, top_n, include_etf).model_dump())


@app.get("/api/v1/signals/{signal_date}")
def signals_by_date(
    signal_date: date,
    strategy: str = Query("composite"),
    top_n: int = Query(20, ge=1, le=100),
    include_etf: bool = Query(False),
):
    return api_response(_get_signals(signal_date, strategy, top_n, include_etf).model_dump())


def _get_signals(signal_date: date, strategy: str, top_n: int, include_etf: bool) -> SignalResponse:
    prev_date = db.execute(
        "SELECT MAX(signal_date) FROM signals WHERE signal_date < ? AND strategy = ?",
        [signal_date, strategy]
    ).fetchone()
    prev = prev_date[0] if prev_date and prev_date[0] else None

    rows = db.execute(
        """SELECT s.stock_id, st.stock_name, s.score, s.rank,
                  m.score AS momentum, v.score AS value, q.score AS quality, g.score AS growth, i.score AS institutional,
                  p.rank AS prev_rank,
                  dp.close, dpy.close AS prev_close
           FROM signals s
           LEFT JOIN stocks st ON s.stock_id = st.stock_id
           LEFT JOIN signals m ON m.signal_date = s.signal_date AND m.stock_id = s.stock_id AND m.strategy = 'momentum'
           LEFT JOIN signals v ON v.signal_date = s.signal_date AND v.stock_id = s.stock_id AND v.strategy = 'value'
           LEFT JOIN signals q ON q.signal_date = s.signal_date AND q.stock_id = s.stock_id AND q.strategy = 'quality'
           LEFT JOIN signals g ON g.signal_date = s.signal_date AND g.stock_id = s.stock_id AND g.strategy = 'growth'
           LEFT JOIN signals i ON i.signal_date = s.signal_date AND i.stock_id = s.stock_id AND i.strategy = 'institutional'
           LEFT JOIN signals p ON p.signal_date = ? AND p.stock_id = s.stock_id AND p.strategy = s.strategy
           LEFT JOIN daily_prices dp ON dp.stock_id = s.stock_id AND dp.trade_date = (SELECT MAX(trade_date) FROM daily_prices WHERE stock_id = s.stock_id)
           LEFT JOIN daily_prices dpy ON dpy.stock_id = s.stock_id AND dpy.trade_date = (SELECT MAX(trade_date) FROM daily_prices WHERE stock_id = s.stock_id AND trade_date < (SELECT MAX(trade_date) FROM daily_prices WHERE stock_id = s.stock_id))
           WHERE s.signal_date = ? AND s.strategy = ?
           ORDER BY s.rank LIMIT ?""",
        [prev, signal_date, strategy, top_n],
    ).fetchall()

    stocks = []
    etfs = []

    # T100: batch-fetch latest PE/PB from valuations table (static fallback)
    pe_pb_map: dict[str, tuple[Optional[float], Optional[float]]] = {}
    stock_ids_set = {r[0] for r in rows}
    if stock_ids_set:
        placeholders = ",".join(["?"] * len(stock_ids_set))
        val_rows = db.execute(
            f"""SELECT DISTINCT ON (v.stock_id) v.stock_id, v.pe_ratio, v.pb_ratio
                FROM valuations v
                WHERE v.stock_id IN ({placeholders}) AND v.trade_date = (
                    SELECT MAX(trade_date) FROM valuations WHERE stock_id = v.stock_id
                )""",
            list(stock_ids_set),
        ).fetchall()
        for vr in val_rows:
            pe_pb_map[vr[0]] = (
                float(vr[1]) if vr[1] and float(vr[1]) > 0 else None,
                float(vr[2]) if vr[2] and float(vr[2]) > 0 else None,
            )

    for r in rows:
        factor_scores = {}
        for i, k in enumerate(['momentum', 'value', 'quality', 'growth', 'institutional']):
            v = r[4 + i]
            if v is not None:
                factor_scores[k] = float(v)

        prev_rank = r[9]
        current_rank = r[3] or 0
        rank_change = (prev_rank - current_rank) if prev_rank is not None else None

        close = float(r[10]) if r[10] else None
        prev_close = float(r[11]) if r[11] else None
        change = round(close - prev_close, 2) if close is not None and prev_close is not None else None
        change_pct = round((change / prev_close) * 100, 2) if change is not None and prev_close else None

        item = SignalItem(
            stock_id=r[0], name=r[1],
            score=float(r[2]) if r[2] else 0,
            rank=current_rank,
            rank_change=rank_change,
            factor_scores=factor_scores if factor_scores else None,
            close_price=close,
            change=change,
            change_pct=change_pct,
            pe=pe_pb_map.get(r[0], (None, None))[0],
            pb=pe_pb_map.get(r[0], (None, None))[1],
        )
        if r[0] in {"0050", "0051", "0052", "0056", "00878", "00881", "006208"}:
            etfs.append(item)
        else:
            stocks.append(item)
    if not include_etf:
        etfs = []
    return SignalResponse(date=signal_date.isoformat(), stocks=stocks[:top_n], etfs=etfs)


@app.post("/api/v1/backtest/run")
def start_backtest(req: BacktestRequest):
    run_id = str(uuid.uuid4())
    start = validate_date_format(req.start_date, "start_date")
    end = validate_date_format(req.end_date, "end_date") if req.end_date else None
    start, end = validate_date_range(start, end)
    weights = req.strategy_weights or DEFAULT_WEIGHTS
    run_backtest(db, start, end, run_id=run_id, strategy_weights=weights, benchmark=req.benchmark, custom_universe=req.custom_universe)
    return api_response(BacktestResponse(run_id=run_id, status="completed").model_dump())


@app.get("/api/v1/backtest/history")
def backtest_history():
    rows = db.execute(
        """SELECT run_id, run_at, start_date, end_date, total_return, cagr, sharpe, max_drawdown, benchmark
           FROM backtest_runs ORDER BY run_at DESC LIMIT 20"""
    ).fetchall()
    return api_response([{
        "run_id": r[0], "created_at": str(r[1]) if r[1] else None,
        "start_date": str(r[2]) if r[2] else None, "end_date": str(r[3]) if r[3] else None,
        "total_return": float(r[4]) if r[4] else None,
        "cagr": float(r[5]) if r[5] else None,
        "sharpe": float(r[6]) if r[6] else None,
        "max_drawdown": float(r[7]) if r[7] else None,
        "benchmark": r[8] if len(r) > 8 and r[8] else "0050",
    } for r in rows])


class BatchDeleteBacktestRequest(BaseModel):
    run_ids: List[str] = Field(..., description="List of run_ids to delete")


@app.delete("/api/v1/backtest/batch")
def delete_backtest_batch(req: BatchDeleteBacktestRequest):
    """Batch delete multiple backtest runs."""
    run_ids = req.run_ids
    if not run_ids:
        raise HTTPException(400, "run_ids cannot be empty")

    deleted = []
    not_found = []

    for run_id in run_ids:
        row = db.execute("SELECT 1 FROM backtest_runs WHERE run_id = ?", [run_id]).fetchone()
        if not row:
            not_found.append(run_id)
            continue
        db.execute("DELETE FROM backtest_equity WHERE run_id = ?", [run_id], read_only=False)
        db.execute("DELETE FROM backtest_runs WHERE run_id = ?", [run_id], read_only=False)
        deleted.append(run_id)
        log.info("backtest_run_deleted_batch", run_id=run_id)

    return api_response({
        "deleted": deleted,
        "not_found": not_found,
        "total_deleted": len(deleted)
    })


@app.delete("/api/v1/backtest/{run_id}")
def delete_backtest(run_id: str):
    row = db.execute("SELECT 1 FROM backtest_runs WHERE run_id = ?", [run_id]).fetchone()
    if not row:
        raise HTTPException(404, "Backtest run not found")
    db.execute("DELETE FROM backtest_equity WHERE run_id = ?", [run_id], read_only=False)
    db.execute("DELETE FROM backtest_runs WHERE run_id = ?", [run_id], read_only=False)
    log.info("backtest_run_deleted", run_id=run_id)
    return api_response({"deleted": run_id})


@app.get("/api/v1/backtest/{run_id}")
def get_backtest(run_id: str):
    row = db.execute(
        """SELECT run_id, run_at, start_date, end_date, strategy_config,
                  total_return, cagr, sharpe, max_drawdown, calmar,
                  turnover, result_path, benchmark
           FROM backtest_runs WHERE run_id = ?""", [run_id]
    ).fetchone()
    if not row:
        raise HTTPException(404, "Backtest run not found")
    return api_response({
        "status": "completed", 
        "run_id": run_id, 
        "benchmark": row[12] if row[12] else "0050",
        "metrics": {
            "total_return": float(row[5]) if row[5] else None,
            "cagr": float(row[6]) if row[6] else None,
            "sharpe": float(row[7]) if row[7] else None,
            "max_drawdown": float(row[8]) if row[8] else None,
            "calmar": float(row[9]) if row[9] else None,
        }
    })


@app.get("/api/v1/data/status")
def data_status():
    latest_price = db.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    stock_count = db.execute("SELECT COUNT(*) FROM stocks").fetchone()
    datasets = db.execute(
        """SELECT dataset, last_status, COUNT(*) as cnt,
                  MAX(last_updated) as last_upd, MAX(bucket) as latest_bucket
           FROM ingestion_tracker
           GROUP BY dataset, last_status
           ORDER BY dataset"""
    ).fetchall()
    signal_dates = db.execute("SELECT COUNT(DISTINCT signal_date), MAX(signal_date) FROM signals").fetchone()
    return api_response({
        "last_price_update": str(latest_price[0]) if latest_price and latest_price[0] else None,
        "stock_count": stock_count[0] if stock_count else 0,
        "signal_dates": signal_dates[0] or 0,
        "latest_signal_date": str(signal_dates[1]) if signal_dates and signal_dates[1] else None,
        "datasets": [{
            "name": r[0],
            "status": r[1],
            "count": r[2],
            "last_updated": str(r[3]) if r[3] else None,
        } for r in datasets],
    })


@app.get("/api/v1/strategies/config")
def strategy_config():
    schemas = get_strategy_schemas()
    return api_response({
        "strategies": {
            name: {
                "params": {k: v["default"] for k, v in p.items()},
                "param_types": {k: v["type"] for k, v in p.items()},
            }
            for name, p in schemas.items()
        },
        "default_weights": DEFAULT_5FACTOR_WEIGHTS,
        "universe_defaults": {
            "include_etf": False,
            "min_market_cap": 3_000_000_000,
            "exclude_financial": True,
            "top_n_stocks": 20,
            "top_n_etfs": 3,
        },
    })


class StrategyRunRequest(BaseModel):
    weights: Optional[dict[str, float]] = None
    strategy_params: Optional[dict[str, dict[str, Any]]] = None
    as_of_date: Optional[str] = None
    top_n_stocks: int = 20
    top_n_etfs: int = 3


@app.post("/api/v1/strategies/run")
def run_strategies(req: StrategyRunRequest):
    from datetime import date as d_date
    as_of = d_date.fromisoformat(req.as_of_date) if req.as_of_date else d_date.today()
    result = compute_composite_scores(
        db, as_of,
        weights=req.weights,
        strategy_params=req.strategy_params,
        top_n_stocks=req.top_n_stocks,
        top_n_etfs=req.top_n_etfs,
    )
    return api_response(result)


class PreviewRequest(StrategyRunRequest):
    holdings: list[dict] = []


@app.post("/api/v1/strategy/preview")
def strategy_preview(req: PreviewRequest):
    from datetime import date as d_date
    from tw_quant_selector.portfolio.preview import preview_impact
    as_of = d_date.fromisoformat(req.as_of_date) if req.as_of_date else d_date.today()
    result = preview_impact(
        db, as_of, req.holdings,
        weights=req.weights,
        strategy_params=req.strategy_params,
        top_n_stocks=req.top_n_stocks,
        top_n_etfs=req.top_n_etfs,
    )
    return api_response(result)


@app.get("/api/v1/strategy/correlation")
def strategy_correlation(as_of_date: Optional[str] = None, lookback_days: int = 252):
    from tw_quant_selector.strategies.correlation import compute_factor_correlation
    from datetime import date as d_date
    as_of = d_date.fromisoformat(as_of_date) if as_of_date else d_date.today()
    matrix = compute_factor_correlation(db, as_of, lookback_days)
    return api_response({"matrix": matrix, "as_of_date": as_of.isoformat()})


class GuruConfigRequest(BaseModel):
    enabled: bool = False
    selected_guru: str = "buffett"
    guru_weight: float = 0.20


_guru_config: dict[str, Any] = {
    "enabled": False,
    "selected_guru": "buffett",
    "guru_weight": 0.20,
}


@app.get("/api/v1/strategy/guru-config")
def get_guru_config():
    from tw_quant_selector.strategies.guru_filters import list_guru_filters
    return api_response({
        **_guru_config,
        "available_gurus": list_guru_filters(),
        "default_5factor_weights": {
            "momentum": 0.25, "value": 0.20,
            "quality": 0.20, "growth": 0.15, "guru": 0.20,
        },
    })


@app.put("/api/v1/strategy/guru-config")
def update_guru_config(req: GuruConfigRequest):
    _guru_config.update({
        "enabled": req.enabled,
        "selected_guru": req.selected_guru,
        "guru_weight": req.guru_weight,
    })
    return api_response(_guru_config)


@app.post("/api/v1/strategy/run-guru-scoring")
def run_guru_scoring():
    from datetime import date as d_date
    from tw_quant_selector.strategies.guru_scoring import run_guru_scoring as _run_scoring
    count = _run_scoring(db, d_date.today())
    return api_response({"scored": count})


@app.get("/api/v1/guru-scores")
def get_guru_scores(
    guru: str = "piotroski",
    date: Optional[str] = None,
    min_score: Optional[int] = None,
    pass_filter: Optional[bool] = None,
    limit: int = 100,
    offset: int = 0,
):
    """Query guru_scores table. Returns F-Score + criteria_detail per stock."""
    from datetime import date as d_date
    score_date = d_date.fromisoformat(date) if date else None

    where = ["guru = :guru"]
    params: dict = {"guru": guru, "limit": limit, "offset": offset}

    if score_date:
        where.append("g.score_date = :score_date")
        params["score_date"] = score_date
    if min_score is not None:
        where.append("g.score >= :min_score")
        params["min_score"] = min_score
    if pass_filter is not None:
        where.append("g.pass_filter = :pass_filter")
        params["pass_filter"] = pass_filter

    rows = db.execute(
        f"""SELECT g.stock_id, s.stock_name, g.score_date, g.score, g.pass_filter, g.criteria_detail
            FROM guru_scores g
            LEFT JOIN stocks s ON g.stock_id = s.stock_id
            WHERE {' AND '.join(where)}
            ORDER BY g.score DESC, g.stock_id
            LIMIT :limit OFFSET :offset""",
        params,
    ).fetchall()

    return api_response([
        {
            "stock_id": r[0],
            "name": r[1],
            "score_date": str(r[2]),
            "score": float(r[3]) if r[3] is not None else None,
            "pass_filter": bool(r[4]) if r[4] is not None else False,
            "criteria_detail": r[5],
        }
        for r in rows
    ])


@app.get("/api/v1/strategy/config-history")
def config_history(limit: int = 10, offset: int = 0):
    rows = db.execute(
        """SELECT * FROM strategy_config_history ORDER BY changed_at DESC LIMIT ? OFFSET ?""",
        [limit, offset],
    ).fetchall()
    # PostgreSQL: 用 information_schema.columns 代替 PRAGMA table_info
    col_rows = db.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'strategy_config_history' ORDER BY ordinal_position
    """).fetchall()
    cols = [r[0] for r in col_rows]
    return api_response([dict(zip(cols, r)) for r in rows])


class SaveConfigRequest(BaseModel):
    weights: dict[str, float] = {}
    advanced_params: dict[str, Any] = {}
    guru_config: dict[str, Any] = {}
    universe_config: dict[str, Any] = {}
    changed_by: str = "user"
    note: str = ""


@app.post("/api/v1/strategy/config-history")
def save_config(req: SaveConfigRequest):
    import json
    db.execute(
        """INSERT INTO strategy_config_history (weights, advanced_params, guru_config, universe_config, changed_by, note)
           VALUES (?, ?, ?, ?, ?, ?)""",
        [json.dumps(req.weights), json.dumps(req.advanced_params),
         json.dumps(req.guru_config), json.dumps(req.universe_config),
         req.changed_by, req.note],
        read_only=False
    )
    return api_response({"saved": True})


@app.delete("/api/v1/strategy/config-history/batch")
def delete_config_batch(config_ids: List[int] = Body(...)):
    if not config_ids:
        raise HTTPException(400, "config_ids cannot be empty")
    placeholders = ",".join(["?"] * len(config_ids))
    db.execute(
        f"DELETE FROM strategy_config_history WHERE config_id IN ({placeholders})",
        config_ids,
        read_only=False,
    )
    log.info("config_history_batch_deleted", count=len(config_ids))
    return api_response({"deleted": config_ids})


@app.delete("/api/v1/strategy/config-history/{config_id}")
def delete_config(config_id: int):
    db.execute("DELETE FROM strategy_config_history WHERE config_id = ?", [config_id], read_only=False)
    return api_response({"deleted": True})


# ── Research & Analysis Endpoints ──────────────────────────────────────

@app.get("/api/v1/stocks/{stock_id}/score-trend")
def stock_score_trend(stock_id: str, days: int = 30):
    sid = normalize_stock_id(stock_id)
    rows = db.execute(
        """SELECT signal_date, strategy, score FROM signals
           WHERE stock_id = ? AND signal_date >= CURRENT_DATE - (:2 * INTERVAL '1 DAY')
           ORDER BY signal_date""",
        [sid, days]
    ).fetchall()
    return api_response([{
        "signal_date": str(r[0]), "strategy": r[1], "score": float(r[2]) if r[2] else None
    } for r in rows])


@app.get("/api/v1/institutional/summary")
def institutional_summary():
    latest = db.execute(
        "SELECT MAX(trade_date) FROM institutional_flows"
    ).fetchone()
    latest_date = latest[0] if latest and latest[0] else None
    if latest_date is None:
        return api_response({
            "foreign_net": 0,
            "sity_net": 0,
            "dealer_net": 0,
            "trade_date": None,
        })
    row = db.execute(
        """SELECT COALESCE(SUM(foreign_investors_net), 0) AS f,
                  COALESCE(SUM(sity_investors_net), 0) AS s,
                  COALESCE(SUM(dealer_net), 0) AS d
           FROM institutional_flows WHERE trade_date = ?""",
        [latest_date]
    ).fetchone()
    return api_response({
        "foreign_net": float(row[0]) if row else 0,
        "sity_net": float(row[1]) if row else 0,
        "dealer_net": float(row[2]) if row else 0,
        "trade_date": str(latest_date),
    })


@app.get("/api/v1/institutional/flows")
def institutional_flows_endpoint(
    stock_id: str, start_date: str = "", end_date: str = ""
):
    sid = normalize_stock_id(stock_id)
    start = validate_date_format(start_date, "start_date") if start_date else date.today() - timedelta(days=30)
    end = validate_date_format(end_date, "end_date") if end_date else date.today()
    rows = db.execute(
        """SELECT f.trade_date, f.foreign_investors_net, f.sity_investors_net,
                  f.dealer_net, f.total_net, dp.close
           FROM institutional_flows f
           LEFT JOIN daily_prices dp ON dp.stock_id = f.stock_id AND dp.trade_date = f.trade_date
           WHERE f.stock_id = ? AND f.trade_date BETWEEN ? AND ?
           ORDER BY f.trade_date""",
        [sid, start, end]
    ).fetchall()
    return api_response([{
        "trade_date": str(r[0]), "foreign_net": float(r[1]) if r[1] else 0,
        "sity_net": float(r[2]) if r[2] else 0, "dealer_net": float(r[3]) if r[3] else 0,
        "total_net": float(r[4]) if r[4] else 0, "close": float(r[5]) if r[5] else None,
    } for r in rows])


@app.get("/api/v1/institutional/top")
def institutional_top(
    top_n: int = Query(10, ge=1, le=100),
    date_: str = Query("", alias="date"),
    sort_by: str = Query("total_net", pattern=r"^(total_net|foreign_investors_net|sity_investors_net|dealer_net)$"),
    order: str = Query("desc", pattern=r"^(asc|desc)$"),
):
    """Top-N stocks by institutional net flow on a given date.

    Returns buy (desc) or sell (asc) ranking of stocks.
    """
    valid_cols = {"total_net", "foreign_investors_net", "sity_investors_net", "dealer_net"}
    col = sort_by if sort_by in valid_cols else "total_net"
    asc_desc = "DESC" if order == "desc" else "ASC"

    if date_:
        td = validate_date_format(date_, "date")
    else:
        td_row = db.execute(
            "SELECT MAX(trade_date) FROM institutional_flows"
        ).fetchone()
        td = td_row[0] if td_row and td_row[0] else date.today()

    rows = db.execute(
        f"""SELECT f.stock_id, st.stock_name, f.foreign_investors_net, f.sity_investors_net,
                   f.dealer_net, f.total_net, dp.close
            FROM institutional_flows f
            JOIN stocks st ON st.stock_id = f.stock_id
            LEFT JOIN daily_prices dp ON dp.stock_id = f.stock_id AND dp.trade_date = f.trade_date
            WHERE f.trade_date = ?
            ORDER BY CASE ?
                WHEN 'total_net' THEN f.total_net
                WHEN 'foreign_investors_net' THEN f.foreign_investors_net
                WHEN 'sity_investors_net' THEN f.sity_investors_net
                WHEN 'dealer_net' THEN f.dealer_net
            END {asc_desc}
            LIMIT ?""",
        [td, col, top_n]
    ).fetchall()

    return api_response({
        "date": str(td),
        "sort_by": col,
        "order": order,
        "data": [{
            "stock_id": r[0], "stock_name": r[1],
            "foreign_net": float(r[2]) if r[2] else 0,
            "sity_net": float(r[3]) if r[3] else 0,
            "dealer_net": float(r[4]) if r[4] else 0,
            "total_net": float(r[5]) if r[5] else 0,
            "close": float(r[6]) if r[6] else None,
        } for r in rows]
    })


class SensitivityRequest(BaseModel):
    start_date: str
    end_date: str = ""
    parameter: str = "momentum_lookback"
    values: list[float] = [60, 120, 180, 252, 360]
    initial_capital: float = 1_000_000
    benchmark: str = "0050"


@app.post("/api/v1/backtest/sensitivity")
def backtest_sensitivity(req: SensitivityRequest):
    from tw_quant_selector.backtest.engine import run_backtest
    from tw_quant_selector.strategies.combiner import DEFAULT_WEIGHTS
    start = validate_date_format(req.start_date, "start_date")
    end = validate_date_format(req.end_date, "end_date") if req.end_date else None
    start, end = validate_date_range(start, end)

    results = []
    for val in req.values:
        weights = dict(DEFAULT_WEIGHTS)
        weights[req.parameter.replace("_lookback", "").replace("_threshold", "").replace("_min_score", "")] = val
        run_id = str(uuid.uuid4())
        run_backtest(db, start, end, run_id=run_id, strategy_weights=weights, benchmark=req.benchmark)
        row = db.execute(
            "SELECT sharpe, cagr, total_return, max_drawdown FROM backtest_runs WHERE run_id = ?",
            [run_id]
        ).fetchone()
        results.append({
            "param_value": val,
            "sharpe": float(row[0]) if row and row[0] else None,
            "cagr": float(row[1]) if row and row[1] else None,
            "total_return": float(row[2]) if row and row[2] else None,
            "max_drawdown": float(row[3]) if row and row[3] else None,
        })
    return api_response(results)


@app.get("/api/v1/factor/ic-analysis")
def factor_ic_analysis(days: int = 365):
    from datetime import date, timedelta
    print(f"[DEBUG] IC analysis: days={days}, today={date.today()}")
    rows = db.execute(
        """SELECT s.signal_date, s.strategy, s.score, dp.close AS cur_close,
                  LEAD(dp.close) OVER (PARTITION BY s.stock_id, s.strategy ORDER BY s.signal_date) AS nxt_close
           FROM signals s
           JOIN daily_prices dp ON dp.stock_id = s.stock_id AND dp.trade_date = s.signal_date
WHERE s.signal_date >= CURRENT_DATE - (:1 * INTERVAL '1 DAY')
                   ORDER BY s.signal_date""",
        [days]
    ).fetchall()
    from collections import defaultdict
    groups: dict = defaultdict(list)
    print(f"[DEBUG] IC analysis: rows count={len(rows)}")
    for r in rows:
        date_str = str(r[0])
        strat = r[1]
        score = float(r[2]) if r[2] else None
        cur = float(r[3]) if r[3] else None
        nxt = float(r[4]) if r[4] else None
        if score is not None and cur and nxt and cur > 0:
            ret = (nxt - cur) / cur
            groups[(date_str, strat)].append((score, ret))
    from scipy.stats import pearsonr
    result = []
    for (dt, strat), vals in sorted(groups.items()):
        if len(vals) < 6:
            continue
        scores, rets = zip(*vals)
        ic, _ = pearsonr(scores, rets)
        result.append({"signal_date": dt, "strategy": strat, "ic": round(ic, 4)})
    return api_response(result)


@app.get("/api/v1/factor/quintile-returns")
def factor_quintile_returns(days: int = 730):
    # Compute T+20 close from daily_prices independently, then join to signals.
    # LEAD(X, 20) over signal_date doesn't work when signals are sparse — instead
    # we find the close 20 trading days ahead in daily_prices for each (stock_id, date).
    rows = db.execute(
        """WITH fut AS (
               SELECT stock_id, trade_date,
                      LEAD(close, 20) OVER (PARTITION BY stock_id ORDER BY trade_date) AS fut_close
               FROM daily_prices
           )
           SELECT s.signal_date, s.strategy, s.score, dp.close AS cur_close, f.fut_close
           FROM signals s
           JOIN daily_prices dp ON dp.stock_id = s.stock_id AND dp.trade_date = s.signal_date
           JOIN fut f ON f.stock_id = s.stock_id AND f.trade_date = s.signal_date
           WHERE s.signal_date >= CURRENT_DATE - (:1 * INTERVAL '1 DAY')""",
        [days]
    ).fetchall()
    import pandas as pd
    df = pd.DataFrame(rows, columns=["signal_date", "strategy", "score", "cur_close", "fut_close"])
    df = df.dropna(subset=["score", "cur_close", "fut_close"])
    if df.empty:
        return api_response([])
    df["future_return"] = (df["fut_close"] - df["cur_close"]) / df["cur_close"]
    df["quintile"] = df.groupby(["signal_date", "strategy"])["score"].transform(
        lambda g: pd.qcut(g, 5, labels=False, duplicates="drop")
    )
    df = df.dropna(subset=["quintile"])
    quintile_avg = df.groupby(["strategy", "quintile"])["future_return"].mean().reset_index()
    return api_response([{
        "strategy": r["strategy"], "quintile": int(r["quintile"]),
        "avg_return": round(float(r["future_return"]) * 100, 2)
    } for _, r in quintile_avg.iterrows()])


@app.get("/api/v1/factor/correlation")
def factor_correlation():
    rows = db.execute(
        """SELECT s.stock_id, s.strategy, s.score FROM signals s
           WHERE s.signal_date = (SELECT MAX(signal_date) FROM signals)"""
    ).fetchall()
    import pandas as pd
    df = pd.DataFrame(rows, columns=["stock_id", "strategy", "score"])
    if df.empty:
        return api_response({})
    pivot = df.pivot_table(index="stock_id", columns="strategy", values="score")
    corr = pivot.corr()
    strategies = list(corr.columns)
    matrix = [
        [round(corr.loc[i, j], 4) if pd.notna(corr.loc[i, j]) else None for j in strategies]
        for i in strategies
    ]
    return api_response({"strategies": strategies, "matrix": matrix})


class InstValidationRequest(BaseModel):
    n_days: int = 10


@app.post("/api/v1/factor/institutional-validation")
def institutional_validation(req: InstValidationRequest):
    n = req.n_days
    rows = db.execute(
        """SELECT i.stock_id, i.trade_date, i.foreign_investors_net + i.sity_investors_net + i.dealer_net AS total_net,
                  dp.close AS entry_price,
                  LEAD(dp.close, ?) OVER (PARTITION BY i.stock_id ORDER BY dp.trade_date) AS exit_price
           FROM institutional_flows i
           JOIN daily_prices dp ON dp.stock_id = i.stock_id AND dp.trade_date = i.trade_date
           WHERE i.trade_date >= CURRENT_DATE - INTERVAL '365 days'
             AND ABS(i.foreign_investors_net + i.sity_investors_net + i.dealer_net) > 10000000
           ORDER BY i.trade_date""",
        [n]
    ).fetchall()
    buy_returns, sell_returns = [], []
    for r in rows:
        total_net = float(r[2]) if r[2] else 0
        entry = float(r[3]) if r[3] else None
        exit_p = float(r[4]) if r[4] else None
        if entry and exit_p and entry > 0:
            ret = (exit_p - entry) / entry
            if total_net > 0:
                buy_returns.append(ret)
            else:
                sell_returns.append(ret)
    return api_response({
        "buy": {"count": len(buy_returns), "avg_excess_return": round(sum(buy_returns) / len(buy_returns), 4)} if buy_returns else {"count": 0, "avg_excess_return": 0},
        "sell": {"count": len(sell_returns), "avg_excess_return": round(sum(sell_returns) / len(sell_returns), 4)} if sell_returns else {"count": 0, "avg_excess_return": 0},
    })


@app.get("/api/v1/alerts/history")
def alert_history(
    severity: str = "", rule_name: str = "",
    start_date: str = "", end_date: str = "",
    unresolved_only: bool = False, limit: int = 200
):
    clauses = ["1=1"]
    params = []
    if severity:
        sev_list = severity.split(",")
        clauses.append(f"severity IN ({','.join('?' for _ in sev_list)})")
        params.extend(sev_list)
    if rule_name:
        clauses.append("rule_name = ?")
        params.append(rule_name)
    if start_date:
        clauses.append("triggered_at::date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("triggered_at::date <= ?")
        params.append(end_date)
    if unresolved_only:
        clauses.append("resolved_at IS NULL")
    rows = db.execute(
        f"""SELECT id, rule_name, severity, message, context_data, triggered_at, resolved_at, resolution_note
            FROM alert_history WHERE {' AND '.join(clauses)}
            ORDER BY triggered_at DESC LIMIT ?""",
        [*params, limit]
    ).fetchall()
    return api_response([{
        "id": r[0], "rule_name": r[1], "severity": r[2], "message": r[3],
        "context_data": r[4], "triggered_at": str(r[5]) if r[5] else None,
        "resolved_at": str(r[6]) if r[6] else None, "resolution_note": r[7],
    } for r in rows])


@app.post("/api/v1/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: str, note: str = ""):
    db.execute(
        "UPDATE alert_history SET resolved_at = NOW(), resolution_note = ? WHERE id = ?",
        [note, alert_id], read_only=False
    )
    return api_response({"resolved": alert_id})


@app.get("/api/v1/alerts/stats")
def alert_stats(start_date: str = "", end_date: str = ""):
    clauses = ["1=1"]
    params = []
    if start_date:
        clauses.append("triggered_at::date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("triggered_at::date <= ?")
        params.append(end_date)
    daily = db.execute(
        f"""SELECT triggered_at::date AS d, severity, COUNT(*) AS cnt
            FROM alert_history WHERE {' AND '.join(clauses)}
            GROUP BY d, severity ORDER BY d""",
        params
    ).fetchall()
    weekly = db.execute(
        f"""SELECT DATE_TRUNC('week', triggered_at)::date AS w, severity, COUNT(*) AS cnt
            FROM alert_history WHERE {' AND '.join(clauses)}
            GROUP BY w, severity ORDER BY w""",
        params
    ).fetchall()
    return api_response({
        "daily": [{"date": str(r[0]), "severity": r[1], "count": r[2]} for r in daily],
        "weekly": [{"week": str(r[0]), "severity": r[1], "count": r[2]} for r in weekly],
    })


@app.get("/api/v1/intraday/{stock_id}")
def intraday_snapshots(stock_id: str):
    sid = normalize_stock_id(stock_id)
    # 1. 優先嘗試今日資料
    rows = db.execute(
        """SELECT snapshot_time, price, volume FROM intraday_snapshots
           WHERE stock_id = ? AND snapshot_time >= CURRENT_DATE
           ORDER BY snapshot_time""",
        [sid]
    ).fetchall()

    # 2. 若今日無資料（週末或尚未開盤），回退到最近有資料的一天
    if not rows:
        rows = db.execute(
            """SELECT snapshot_time, price, volume FROM intraday_snapshots
               WHERE stock_id = ? AND snapshot_time >= (
                   SELECT MAX(snapshot_time::date) FROM intraday_snapshots WHERE stock_id = ?
               )
               ORDER BY snapshot_time""",
            [sid, sid]
        ).fetchall()

    return api_response([{
        "snapshot_time": str(r[0]), "price": float(r[1]) if r[1] else None,
        "volume": int(r[2]) if r[2] else 0,
    } for r in rows])


@app.get("/api/v1/intraday/{stock_id}/kline")
def intraday_kline_api(stock_id: str, period_min: int = 60, days: int = 1):
    """Return intraday K-line data for a stock."""
    sid = normalize_stock_id(stock_id)
    cutoff = date.today() - timedelta(days=days)
    rows = db.execute(
        """SELECT k_time, period_min, open, high, low, close, volume
           FROM intraday_kline
           WHERE stock_id = ? AND period_min = ? AND k_time::date >= ?
           ORDER BY k_time ASC""",
        [sid, period_min, cutoff]
    ).fetchall()
    return api_response([{
        "k_time": str(r[0]), "period_min": r[1],
        "open": float(r[2]) if r[2] else None,
        "high": float(r[3]) if r[3] else None,
        "low": float(r[4]) if r[4] else None,
        "close": float(r[5]) if r[5] else None,
        "volume": int(r[6]) if r[6] else 0,
    } for r in rows])


# ── Strategy Config History (T127) ──


class StrategyConfigUpdateRequest(BaseModel):
    weights: dict[str, float] = Field(..., description="Factor weights (momentum, value, quality, growth, institutional)")
    advanced_params: Optional[dict[str, Any]] = None
    guru_config: Optional[dict[str, Any]] = None
    universe_config: Optional[dict[str, Any]] = None
    note: str = ""

    @field_validator('weights')
    @classmethod
    def validate_weights(cls, v: dict[str, float]) -> dict[str, float]:
        total = sum(v.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0 (got {total:.4f})")
        for name, w in v.items():
            if w < 0 or w > 1:
                raise ValueError(f"Weight for '{name}' must be between 0 and 1")
        return v


@app.get("/api/v1/strategy/config")
def get_strategy_config():
    """Return current strategy config from YAML file."""
    import yaml
    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "strategy_weights_6factor.yaml"

    if not config_path.exists():
        raise HTTPException(404, "Strategy config file not found")

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    return api_response(config)


@app.put("/api/v1/strategy/config")
def update_strategy_config(req: StrategyConfigUpdateRequest):
    """Update strategy config and save snapshot to history.

    Atomic: saves current config snapshot first, then writes new config to YAML.
    """
    import yaml
    from datetime import date
    import json as json_mod

    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "strategy_weights_6factor.yaml"

    # Read current config (before update)
    current_config = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            current_config = yaml.safe_load(f) or {}

    # Save snapshot of current config before overwriting
    try:
        with db.connection(read_only=False) as conn:
            conn.execute(
                text(
                    """INSERT INTO strategy_config_history
                       (weights, advanced_params, guru_config, universe_config,
                        changed_by, note)
                       VALUES (:weights, :advanced, :guru, :universe,
                               :changed_by, :note)"""
                ),
                {
                    "weights": json_mod.dumps(current_config.get("weights", {})),
                    "advanced": json_mod.dumps(current_config.get("advanced_params", {})),
                    "guru": json_mod.dumps(current_config.get("guru_config", {})),
                    "universe": json_mod.dumps(current_config.get("universe_config", {})),
                    "changed_by": "api",
                    "note": req.note or "manual config update via API",
                },
            )
            conn.commit()
            log.info("strategy_config.snapshot_saved", note=req.note)
    except Exception as e:
        log.error("strategy_config.snapshot_failed", error=str(e))
        raise HTTPException(500, f"Failed to save config snapshot: {e}")

    # Build new config
    new_config = {
        "weights": req.weights,
        "advanced_params": req.advanced_params or current_config.get("advanced_params", {}),
        "guru_config": req.guru_config or current_config.get("guru_config", {}),
        "universe_config": req.universe_config or current_config.get("universe_config", {}),
    }

    # Write new config to YAML
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(new_config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    log.info("strategy_config.updated", weights=req.weights)
    return api_response(new_config)


@app.get("/api/v1/strategy/config/history")
def get_strategy_config_history(limit: int = 50):
    """Return strategy config change history."""
    rows = db.execute(
        """SELECT config_id, changed_at, weights, advanced_params, guru_config,
                  universe_config, changed_by, note
           FROM strategy_config_history
           ORDER BY changed_at DESC
           LIMIT :limit""",
        {"limit": limit},
    ).fetchall()

    return api_response([
        {
            "config_id": r[0],
            "changed_at": str(r[1]) if r[1] else None,
            "weights": r[2],
            "advanced_params": r[3],
            "guru_config": r[4],
            "universe_config": r[5],
            "changed_by": r[6],
            "note": r[7],
        }
        for r in rows
    ])


@app.get("/api/v1/screener")
def get_screener():
    """
    Unified screener data: EPS, ROE, PE, PB, dividend_yield, CAGR, price per stock.
    Replaces multiple external API calls the frontend was making.
    """
    data, cached_at, latest_date, warming = screener_module.get_data()
    if data is None:
        raise HTTPException(503, detail={"status": "warming", "message": "Screener data is being loaded, try again shortly"})
    return api_response(
        data={"stocks": data, "count": len(data)},
        meta={"cached_at": cached_at.isoformat() if cached_at else None, "date": latest_date},
    )


@app.get("/api/v1/cagr")
def get_cagr():
    data, cached_at, warming = cagr_module.get_data()
    if data is None:
        if warming:
            raise HTTPException(503, detail={"status": "warming", "message": "CAGR data is being fetched, try again shortly"})
        raise HTTPException(404, detail={"status": "unavailable", "message": "No CAGR data available"})
    return api_response(
        data={"stocks": data, "count": len(data)},
        meta={"cached_at": cached_at.isoformat() if cached_at else None},
    )


# ── Market Dashboard (Quotes) ──
import yfinance as yf

_MARKET_SYMBOLS = {
    "^DJI":   {"name": "道瓊工業平均指數",   "en": "Dow Jones",            "section": "us_index"},
    "^GSPC":  {"name": "標準普爾 500 指數",   "en": "S&P 500",              "section": "us_index"},
    "^IXIC":  {"name": "那斯達克綜合指數",    "en": "NASDAQ Composite",     "section": "us_index"},
    "^SOX":   {"name": "費城半導體指數",       "en": "PHLX Semiconductor",   "section": "us_index"},
    "^SKEW":  {"name": "黑天鵝指數",           "en": "CBOE SKEW",            "section": "us_index"},
    "MU":     {"name": "美光科技",   "en": "Micron",    "section": "us_stocks"},
    "INTC":   {"name": "英特爾",     "en": "Intel",     "section": "us_stocks"},
    "AMD":    {"name": "超微半導體", "en": "AMD",       "section": "us_stocks"},
    "AVGO":   {"name": "博通",       "en": "Broadcom",  "section": "us_stocks"},
    "NVDA":   {"name": "輝達",       "en": "Nvidia",    "section": "us_stocks"},
    "TSM":    {"name": "台積電 ADR", "en": "TSMC ADR",  "section": "us_stocks"},
    "UMC":    {"name": "聯電 ADR",   "en": "UMC ADR",   "section": "us_stocks"},
    "ASX":    {"name": "日月光 ADR", "en": "ASE ADR",   "section": "us_stocks"},
    "CL=F":   {"name": "紐約輕原油", "en": "WTI Crude Oil",   "section": "oil"},
    "BZ=F":   {"name": "布蘭特原油", "en": "Brent Crude Oil", "section": "oil"},
    "DX-Y.NYB": {"name": "美元指數",        "en": "US Dollar Index (DXY)",      "section": "gold"},
    "GC=F":     {"name": "黃金期貨",         "en": "Gold Futures",               "section": "gold"},
    "TIP":      {"name": "抗通膨債券",       "en": "iShares TIPS Bond ETF",      "section": "gold"},
    "^TNX":     {"name": "10年期公債殖利率", "en": "10Y Treasury Yield",         "section": "gold"},
    "ZQ=F":     {"name": "聯邦基金期貨",     "en": "Fed Funds Futures",          "section": "gold"},
    "^TWII":    {"name": "台灣加權指數",       "en": "TAIEX",        "section": "tw"},
    "^TWO":     {"name": "台灣櫃檯指數",       "en": "TPEx OTC",     "section": "tw"},
    "2330.TW":  {"name": "台積電（參考）",     "en": "TSMC TW",      "section": "tw"},
}


def _fetch_market_quote(symbol: str) -> dict | None:
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        price = getattr(info, "last_price", None)
        prev  = getattr(info, "previous_close", None)
        if not price:
            hist = ticker.history(period="2d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
            prev  = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
        if not price:
            return None
        prev   = prev or price
        change = price - prev
        pct    = (change / prev * 100) if prev else 0
        return {
            "price":     round(price, 4),
            "prev":      round(prev, 4),
            "change":    round(change, 4),
            "changePct": round(pct, 4),
        }
    except Exception:
        return None


@app.get("/api/market/quotes")
def market_quotes():
    result = {}
    for symbol, meta in _MARKET_SYMBOLS.items():
        data = _fetch_market_quote(symbol)
        result[symbol] = {
            "symbol":  symbol,
            "name":    meta["name"],
            "en":      meta["en"],
            "section": meta["section"],
            "data":    data,
        }
    return {
        "ok":        True,
        "updatedAt": datetime.now().isoformat(),
        "quotes":    result,
    }


@app.get("/api/market/quote/{symbol}")
def market_quote_single(symbol: str):
    symbol = symbol.upper()
    if symbol not in _MARKET_SYMBOLS:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    data = _fetch_market_quote(symbol)
    return {"ok": True, "symbol": symbol, "data": data}


@app.get("/api/market/health")
def market_health():
    return {"ok": True, "time": datetime.now().isoformat()}


# ── Serve Built Frontend (Docker / production) ──
_frontend_dist = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
if _frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_frontend_dist / "assets")), name="frontend_assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith("api/") or full_path.startswith("health") or full_path in ("docs", "redoc", "openapi.json"):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": {"code": 404, "message": "Not found"}}, status_code=404)
        index = _frontend_dist / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>Frontend not built</h1>", status_code=200)


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>tw-quant-selector</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}
h1{font-size:1.4rem;color:#38bdf8;cursor:pointer;display:inline-block}
h2{font-size:1.1rem;margin:20px 0 10px;color:#94a3b8}
.sub{color:#64748b;font-size:.85rem;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:20px}
.card{background:#1e293b;border-radius:10px;padding:16px;border:1px solid #334155}
.card .label{font-size:.8rem;color:#64748b}
.card .value{font-size:1.5rem;font-weight:700;margin-top:4px}
.value.green{color:#22c55e}.value.yellow{color:#eab308}.value.blue{color:#38bdf8}
.tabs{display:flex;gap:4px;margin:16px 0;border-bottom:2px solid #1e293b}
.tab{padding:8px 18px;cursor:pointer;border-radius:8px 8px 0 0;color:#64748b;font-size:.9rem;font-weight:600;transition:.15s}
.tab:hover{color:#e2e8f0;background:#1e293b}
.tab.active{color:#38bdf8;background:#1e293b;border-bottom:2px solid #38bdf8;margin-bottom:-2px}
.tab-pane{display:none}
.tab-pane.active{display:block}
table{width:100%;border-collapse:collapse;font-size:.85rem}
th{text-align:left;padding:8px 10px;border-bottom:2px solid #334155;color:#94a3b8;font-weight:600}
td{padding:6px 10px;border-bottom:1px solid #1e293b}
tr{cursor:pointer}tr:hover td{background:#1e293b}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.75rem;font-weight:600}
.badge.ok{background:#166534;color:#86efac}
.badge.failed{background:#7f1d1d;color:#fca5a5}
.badge.skipped{background:#713f12;color:#fde047}
.badge.running{background:#1e3a5f;color:#93c5fd}
.clickable{color:#38bdf8;text-decoration:underline;cursor:pointer}
.loading{color:#64748b;font-style:italic}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:100}
.modal{display:none;position:fixed;top:5%;left:5%;right:5%;bottom:5%;background:#0f172a;border:1px solid #334155;border-radius:12px;z-index:101;overflow:auto;padding:24px}
.modal-close{float:right;background:none;border:none;color:#94a3b8;font-size:1.5rem;cursor:pointer}
.modal-close:hover{color:#fff}
canvas{width:100%;height:280px;background:#1e293b;border-radius:8px;margin:12px 0}
input.search,select.search{width:100%;padding:10px 14px;background:#1e293b;border:1px solid #334155;border-radius:8px;color:#e2e8f0;font-size:1rem;margin-bottom:16px;outline:none}
input.search:focus,select.search:focus{border-color:#38bdf8}
input[type=number]{width:100%;padding:8px 10px;background:#1e293b;border:1px solid #334155;border-radius:6px;color:#e2e8f0;font-size:.85rem;outline:none}
input[type=number]:focus{border-color:#38bdf8}
label{font-size:.8rem;color:#94a3b8;display:block;margin-bottom:2px}
.ctrl-row{align-items:center;display:flex;gap:16px;margin-bottom:12px}
.ctrl-row label{flex:0 0 120px;margin:0}
.ctrl-row input,.ctrl-row select{flex:1}
.ctrl-row .val-label{min-width:50px;text-align:right;color:#e2e8f0;font-weight:600;font-size:.85rem}
.btn{padding:10px 24px;border:none;border-radius:8px;font-size:.95rem;font-weight:600;cursor:pointer;transition:.15s;display:inline-flex;align-items:center;gap:6px}
.btn-primary{background:#0b6bcb;color:#fff}
.btn-primary:hover{background:#0954a0}
.btn-primary:disabled{opacity:.5;cursor:not-allowed}
.btn-success{background:#166534;color:#fff}
.btn-success:hover{background:#14532d}
.btn-warning{background:#92400e;color:#fff}
.btn-warning:hover{background:#78350f}
.section{border:1px solid #334155;border-radius:10px;padding:16px;margin-bottom:16px}
.section h3{font-size:.95rem;color:#e2e8f0;margin-bottom:10px}
input[type=range]{width:100%;-webkit-appearance:none;background:#1e293b;height:6px;border-radius:3px;outline:none}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;background:#38bdf8;cursor:pointer}
.param-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}
.result-table{max-height:500px;overflow-y:auto}
@media(max-width:640px){.grid{grid-template-columns:repeat(2,1fr)}body{padding:12px}.modal{top:2%;left:2%;right:2%;bottom:2%;padding:16px}.ctrl-row{flex-wrap:wrap;gap:6px}.ctrl-row label{flex:0 0 80px}}
</style>
</head>
<body>
<div class="modal-overlay" id="modal-overlay" onclick="closeModal()"></div>
<div class="modal" id="modal"><button class="modal-close" onclick="closeModal()">&times;</button><div id="modal-body">載入中...</div></div>

<h1 onclick="location.href='/'">tw-quant-selector</h1>
<p class="sub" id="sub">載入中...</p>

<div class="tabs">
  <div class="tab active" data-tab="dash" onclick="switchTab('dash')">📊 儀表板(Dashboard)</div>
  <div class="tab" data-tab="strategy" onclick="switchTab('strategy')">⚙️ 策略(Strategy)</div>
  <div class="tab" data-tab="backtest" onclick="switchTab('backtest')">📈 回測(Backtest)</div>
</div>

<!-- ══════ TAB 1: DASHBOARD ══════ -->
<div class="tab-pane active" id="tab-dash">
  <div class="grid" id="stats-grid"></div>
  <h2>🔍 查詢股票(Search)</h2>
  <input class="search" placeholder="輸入股號或名稱 (2330、台積電…)" oninput="searchStock(this.value)">
  <table><thead><tr><th>股號(ID)</th><th>名稱(Name)</th><th>市場(Market)</th><th>類型(Type)</th></tr></thead><tbody id="search-body"></tbody></table>
  <h2>📋 資料擷取(Ingestion Tracker)</h2>
  <table><thead><tr><th>資料集(Dataset)</th><th>狀態(Status)</th><th>筆數(Count)</th></tr></thead><tbody id="tracker-body"></tbody></table>
  <h2>🏆 最多資料的股票(Top Stocks)</h2>
  <table><thead><tr><th>股號(ID)</th><th>交易日數(Days)</th></tr></thead><tbody id="top-body"></tbody></table>
  <h2>📈 最新訊號(Latest Signals)</h2>
  <div class="ctrl-row">
    <label>選擇策略(Strategy)</label>
    <select id="dash-strategy" class="search" style="margin-bottom:0" onchange="loadDashboardSignals()">
      <option value="composite">綜合(Composite)</option>
      <option value="momentum">動能(Momentum)</option>
      <option value="value">價值(Value)</option>
      <option value="quality">品質(Quality)</option>
      <option value="growth">成長(Growth)</option>
    </select>
  </div>
  <table id="dash-signal-table">
    <thead>
      <tr>
        <th onclick="sortSignals('stock_id')">股號 ⇅</th>
        <th onclick="sortSignals('name')">名稱 ⇅</th>
        <th onclick="sortSignals('score')">分數 ⇅</th>
        <th onclick="sortSignals('rank')">排名 ⇅</th>
        <th onclick="sortSignals('m')">動能 ⇅</th>
        <th onclick="sortSignals('v')">價值 ⇅</th>
        <th onclick="sortSignals('q')">品質 ⇅</th>
        <th onclick="sortSignals('g')">成長 ⇅</th>
      </tr>
    </thead>
    <tbody id="signal-body"></tbody>
  </table>
</div>

<!-- ══════ TAB 2: STRATEGY CONTROL ══════ -->
<div class="tab-pane" id="tab-strategy">
  <div class="section" id="strategy-weights-section"><h3>🏋️ 策略權重(Weights)</h3></div>
  <div class="section" id="strategy-params-section"><h3>🔧 策略參數(Parameters)</h3></div>
  <div class="section">
    <h3>📋 篩選條件(Universe Filters)</h3>
    <div class="ctrl-row"><label>納入ETF(Include ETF)</label>
      <select id="uf-include-etf"><option value="false">否(No)</option><option value="true">是(Yes)</option></select>
    </div>
    <div class="ctrl-row"><label>最低市值(Min Market Cap)(億)</label>
      <input type="number" id="uf-min-cap" value="30">
    </div>
    <div class="ctrl-row"><label>前N檔股票(Top N Stocks)</label>
      <input type="number" id="uf-top-stocks" value="20" min="1" max="100">
    </div>
    <div class="ctrl-row"><label>前N檔ETF(Top N ETFs)</label>
      <input type="number" id="uf-top-etfs" value="3" min="0" max="20">
    </div>
    <div class="ctrl-row"><label>評分日期(Score Date)</label>
      <input type="date" id="uf-as-of">
    </div>
    <div style="margin-top:16px">
      <button class="btn btn-primary" id="btn-run-strategy" onclick="runStrategy()">▶ 執行評分(Run)</button>
      <span id="run-status" style="margin-left:12px;color:#64748b;font-size:.85rem"></span>
    </div>
  </div>
  <div class="section" id="result-section" style="display:none">
    <h3>✅ 評分結果(Results)</h3>
    <div class="result-table" id="result-body"></div>
  </div>
</div>

<!-- ══════ TAB 3: BACKTEST ══════ -->
<div class="tab-pane" id="tab-backtest">
  <div class="section">
    <h3>📈 回測設定(Backtest Settings)</h3>
    <div class="ctrl-row"><label>開始日期(Start)</label><input type="date" id="bt-start"></div>
    <div class="ctrl-row"><label>結束日期(End)</label><input type="date" id="bt-end"></div>
    <div style="margin-top:16px">
      <button class="btn btn-warning" onclick="runBacktest()">▶ 執行回測(Run Backtest)</button>
      <span id="bt-status" style="margin-left:12px;color:#64748b;font-size:.85rem"></span>
    </div>
  </div>
  <div class="section" id="bt-result-section" style="display:none">
    <h3>📊 回測結果(Results)</h3>
    <div id="bt-result-body"></div>
  </div>
  <div class="section">
    <h3>📜 歷史回測(History)</h3>
    <table><thead><tr><th>回測ID(Run ID)</th><th>區間(Period)</th><th>報酬率(Return)</th><th>CAGR</th><th>Sharpe</th><th>最大回撤(Max DD)</th></tr></thead><tbody id="bt-history-body"></tbody></table>
  </div>
</div>

<script>
let config = {};
let latestResult = null;

// ── Tab switching ──
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.toggle('active', p.id === 'tab-'+name));
}

// ── Modal ──
function openModal(html) {
  document.getElementById('modal-overlay').style.display = 'block';
  document.getElementById('modal').style.display = 'block';
  document.getElementById('modal-body').innerHTML = html;
}
function closeModal() {
  document.getElementById('modal-overlay').style.display = 'none';
  document.getElementById('modal').style.display = 'none';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// ── Stock search ──
let searchTimer = null;
async function searchStock(q) {
  clearTimeout(searchTimer);
  const tb = document.getElementById('search-body');
  if (!q || q.length < 1) { tb.innerHTML = ''; return; }
  searchTimer = setTimeout(async () => {
    const rows = await fetch('/api/v1/stocks/search?q='+encodeURIComponent(q)).then(r=>r.json());
    tb.innerHTML = rows.map(r =>
      `<tr onclick="openStock('${r.stock_id}')"><td class="clickable">${r.stock_id}</td><td>${r.name}</td><td>${r.market}</td><td>${r.is_etf?'ETF':'個股(Stock)'}</td></tr>`
    ).join('');
  }, 200);
}

async function openStock(sid) {
  openModal('<p class="loading">載入中...</p>');
  try {
    const d = await fetch('/api/v1/stock/'+sid).then(r=>r.json());
    const i = d.info;
    let html = `<h2>${i.name} (${i.stock_id}) <span class="badge ${i.is_etf?'ok':'skipped'}">${i.is_etf?'ETF':'個股(Stock)'}</span> ${i.market} ${i.industry||''}</h2>`;
    if (d.prices.length) html += `<canvas id="pc"></canvas>`;
    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">`;
    if (d.valuations.length) {
      html += `<div style="grid-column:1/-1"><h3>📊 本益比/淨值比(PE/PB)</h3><table><thead><tr><th>日期(Date)</th><th>PE</th><th>PB</th><th>殖利率(Dividend Yield)</th></tr></thead><tbody>`;
      for (const v of d.valuations) html += `<tr><td>${v.d}</td><td>${v.pe??'-'}</td><td>${v.pb??'-'}</td><td>${v.dy!=null?(v.dy*100).toFixed(2)+'%':'-'}</td></tr>`;
      html += `</tbody></table></div>`;
    }
    if (d.financials.length) {
      html += `<div style="grid-column:1/-1"><h3>💰 財報(Financials)</h3><table><thead><tr><th>季度(Quarter)</th><th>營收(Revenue)</th><th>EPS</th><th>ROE</th><th>毛利率(Gross Margin)</th><th>負債比(D/E)</th></tr></thead><tbody>`;
      for (const f of d.financials) html += `<tr><td>${f.yq}</td><td>${f.rev!=null?Number(f.rev).toLocaleString():'-'}</td><td>${f.eps??'-'}</td><td>${f.roe!=null?(f.roe*100).toFixed(2)+'%':'-'}</td><td>${f.gm!=null?(f.gm*100).toFixed(2)+'%':'-'}</td><td>${f.de!=null?f.de.toFixed(2):'-'}</td></tr>`;
      html += `</tbody></table></div>`;
    }
    if (d.revenue.length) {
      html += `<div style="grid-column:1/-1"><h3>📅 月營收(Monthly Revenue)</h3><table><thead><tr><th>月份(Month)</th><th>營收(Revenue)</th><th>年增率(YoY)</th></tr></thead><tbody>`;
      for (const r of d.revenue) html += `<tr><td>${r.ym}</td><td>${r.rev!=null?Number(r.rev).toLocaleString():'-'}</td><td>${r.yoy!=null?(r.yoy*100).toFixed(2)+'%':'-'}</td></tr>`;
      html += `</tbody></table></div>`;
    }
    html += `</div>`;
    document.getElementById('modal-body').innerHTML = html;
    if (d.prices.length) setTimeout(() => drawChart(d.prices.reverse()), 50);
  } catch(e) { document.getElementById('modal-body').innerHTML = '<p>❌ 查無此股票</p>'; }
}

function drawChart(prices) {
  const c = document.getElementById('pc'); if (!c) return;
  const ctx = c.getContext('2d');
  const dpr = window.devicePixelRatio||1;
  const rect = c.getBoundingClientRect();
  c.width = rect.width*dpr; c.height = rect.height*dpr;
  ctx.scale(dpr,dpr);
  const w=rect.width, h=rect.height, pad={t:20,r:16,b:30,l:50}, cw=w-pad.l-pad.r, ch=h-pad.t-pad.b;
  const cls = prices.map(p=>p.c).filter(x=>x!=null);
  if (!cls.length) return;
  const mn=Math.min(...cls), mx=Math.max(...cls), range=mx-mn||1;
  ctx.clearRect(0,0,w,h); ctx.strokeStyle='#38bdf8'; ctx.lineWidth=2; ctx.beginPath();
  prices.forEach((p,i)=>{const x=pad.l+(i/(prices.length-1||1))*cw, y=pad.t+(1-(p.c-mn)/range)*ch; i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);});
  ctx.stroke();
  ctx.fillStyle='#64748b'; ctx.font='11px -apple-system, sans-serif'; ctx.textAlign='center';
  const step=Math.max(1,Math.floor(prices.length/8));
  prices.forEach((p,i)=>{if(i%step===0||i===prices.length-1)ctx.fillText(p.d.slice(5),pad.l+(i/(prices.length-1||1))*cw,h-6);});
  ctx.textAlign='right';
  for(let v=Math.floor(mn/10)*10;v<=mx;v+=Math.max(1,Math.round(range/4))){const y=pad.t+(1-(v-mn)/range)*ch;ctx.fillText(v.toFixed(0),pad.l-4,y+4);ctx.strokeStyle='#1e293b';ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();}
}

async function openDataset(ds) {
  openModal('<p class="loading">載入中...</p>');
  try {
    const rows = await fetch('/api/v1/stocks/by_dataset/'+ds).then(r=>r.json());
    const labels={daily_prices:'股價(Prices)',valuations:'本益比/淨值比(PE/PB)',monthly_revenue:'月營收(Revenue)',financials:'財報(Financials)'};
    let html=`<h2>📁 ${labels[ds]||ds}</h2><p>共(Total) <strong>${rows.length}</strong> 檔(stocks)有資料</p><table><thead><tr><th>股號(ID)</th><th>名稱(Name)</th><th>市場(Market)</th><th>筆數(Count)</th></tr></thead><tbody>`;
    for(const r of rows) html+=`<tr onclick="openStock('${r.stock_id}')"><td class="clickable">${r.stock_id}</td><td>${r.name}</td><td>${r.market}</td><td>${r.count}</td></tr>`;
    html+=`</tbody></table>`;
    document.getElementById('modal-body').innerHTML = html;
  } catch(e) { document.getElementById('modal-body').innerHTML = '<p>❌ 查詢失敗</p>'; }
}

// ── Dashboard load ──
async function loadDashboard() {
  const d = await fetch('/api/v1/dashboard').then(r=>r.json());
  document.getElementById('sub').textContent =
    `  股價(Prices) ${d.price_date_range.min||'?'} ~ ${d.price_date_range.max||'?'} · 本益比(PE/PB) ${d.val_date_range.min||'?'} ~ ${d.val_date_range.max||'?'}`;
  const labels={stocks:'股票(Stocks)',daily_prices:'股價(Prices)',valuations:'本益比(PE/PB)',monthly_revenue:'月營收(Revenue)',financials:'財報(Financials)',signals:'訊號(Signals)',backtest_runs:'回測(Backtests)'};
  document.getElementById('stats-grid').innerHTML = '';
  for(const[k,v]of Object.entries(d.table_counts)){
    const color=k==='stocks'?'blue':k==='daily_prices'?'green':'yellow';
    document.getElementById('stats-grid').innerHTML+=`<div class="card"><div class="label">${labels[k]||k}</div><div class="value ${color}">${v.toLocaleString()}</div></div>`;
  }
  const dsLabels={daily_prices:'股價(Prices)',valuations:'本益比/淨值比(PE/PB)',monthly_revenue:'月營收(Revenue)',financials:'財報(Financials)',signals:'訊號(Signals)'};
  document.getElementById('tracker-body').innerHTML = '';
  for(const r of d.tracker)
    document.getElementById('tracker-body').innerHTML+=`<tr onclick="openDataset('${r.dataset}')"><td class="clickable">${dsLabels[r.dataset]||r.dataset}</td><td><span class="badge ${r.status}">${r.status=='ok'?'成功(OK)':r.status=='failed'?'失敗(Failed)':r.status=='skipped'?'略過(Skipped)':r.status}</span></td><td>${r.count}</td></tr>`;
  document.getElementById('top-body').innerHTML = '';
  for(const r of d.top_stocks)
    document.getElementById('top-body').innerHTML+=`<tr onclick="openStock('${r.stock_id}')"><td class="clickable">${r.stock_id}</td><td>${r.days}天</td></tr>`;
  loadDashboardSignals();
}

let dashSignals = [];
let sortState = { key: 'rank', asc: true };

async function loadDashboardSignals() {
  const strat = document.getElementById('dash-strategy').value;
  document.getElementById('signal-body').innerHTML = '<tr><td colspan="8" class="loading">載入中...</td></tr>';
  const s = await fetch(`/api/v1/signals/latest?strategy=${strat}&include_etf=true&top_n=100`).then(r=>r.json()).catch(()=>null);
  if (s) {
    dashSignals = [...(s.stocks || []), ...(s.etfs || [])];
    renderSignalTable();
  } else {
    document.getElementById('signal-body').innerHTML = '<tr><td colspan="8" class="loading">尚無訊號</td></tr>';
  }
}

function renderSignalTable() {
  const tb = document.getElementById('signal-body');
  const sorted = [...dashSignals].sort((a, b) => {
    let valA, valB;
    if (sortState.key === 'm') { valA = a.factor_scores?.momentum || 0; valB = b.factor_scores?.momentum || 0; }
    else if (sortState.key === 'v') { valA = a.factor_scores?.value || 0; valB = b.factor_scores?.value || 0; }
    else if (sortState.key === 'q') { valA = a.factor_scores?.quality || 0; valB = b.factor_scores?.quality || 0; }
    else if (sortState.key === 'g') { valA = a.factor_scores?.growth || 0; valB = b.factor_scores?.growth || 0; }
    else { valA = a[sortState.key]; valB = b[sortState.key]; }
    
    if (typeof valA === 'string') return sortState.asc ? valA.localeCompare(valB) : valB.localeCompare(valA);
    return sortState.asc ? valA - valB : valB - valA;
  });

  tb.innerHTML = sorted.map(item => `
    <tr onclick="openStock('${item.stock_id}')">
      <td class="clickable">${item.stock_id}</td>
      <td>${item.name || '-'}</td>
      <td>${item.score.toFixed(4)}</td>
      <td>#${item.rank}</td>
      <td>${item.factor_scores?.momentum?.toFixed(2) || '-'}</td>
      <td>${item.factor_scores?.value?.toFixed(2) || '-'}</td>
      <td>${item.factor_scores?.quality?.toFixed(2) || '-'}</td>
      <td>${item.factor_scores?.growth?.toFixed(2) || '-'}</td>
    </tr>
  `).join('');
}

function sortSignals(key) {
  if (sortState.key === key) sortState.asc = !sortState.asc;
  else { sortState.key = key; sortState.asc = true; }
  renderSignalTable();
}

const STRAT_LABELS = {
  momentum:'動能(Momentum)', value:'價值(Value)', quality:'品質(Quality)', growth:'成長(Growth)',
  lookback_long:'回看天數(Lookback Long)', lookback_short:'短天期(Lookback Short)', min_data_days:'最少天數(Min Data)',
  max_pb:'最高PB(Max PB)', max_pe:'最高PE(Max PE)', min_yield:'最低殖利率(Min Yield)',
  roe_weight:'ROE權重(ROE Weight)', leverage_weight:'槓桿權重(Leverage Weight)', stability_weight:'穩定性權重(Stability Weight)', lookback_quarters:'回看季度(Quarters)',
  rev_weight:'營收權重(Rev Weight)', eps_weight:'EPS權重(EPS Weight)', rev_months:'營收月數(Rev Months)',
};

// ── Strategy Control ──
async function loadStrategyConfig() {
  const c = await fetch('/api/v1/strategies/config').then(r=>r.json());
  config = c;
  const wsHtml = Object.entries(c.default_weights).map(([k,v]) =>
    `<div class="ctrl-row"><label>${STRAT_LABELS[k]||k}</label>
      <input type="range" min="0" max="100" value="${Math.round(v*100)}" id="w-${k}" oninput="updateWeightLabel('${k}')">
      <span class="val-label" id="wl-${k}">${(v*100).toFixed(0)}%</span></div>`
  ).join('');
  document.querySelector('#strategy-weights-section').innerHTML = `<h3>🏋️ 策略權重(Weights)</h3>${wsHtml}`;

  let paramsHtml = '';
  for (const [name, s] of Object.entries(c.strategies)) {
    paramsHtml += `<div style="margin-bottom:12px"><strong style="color:#38bdf8">${STRAT_LABELS[name]||name}</strong>`;
    paramsHtml += `<div class="param-grid">`;
    for (const [pn, pv] of Object.entries(s.params)) {
      const t = s.param_types[pn] || 'number';
      paramsHtml += `<div><label>${STRAT_LABELS[pn]||pn}</label><input type="${t==='number'?'number':'text'}" value="${pv}" id="sp-${name}-${pn}" style="width:100%"></div>`;
    }
    paramsHtml += `</div></div>`;
  }
  document.querySelector('#strategy-params-section').innerHTML = `<h3>🔧 策略參數(Parameters)</h3>${paramsHtml}`;

  // Set default date
  document.getElementById('uf-as-of').value = new Date().toISOString().slice(0,10);
}

function updateWeightLabel(name) {
  document.getElementById('wl-'+name).textContent = document.getElementById('w-'+name).value + '%';
}

async function runStrategy() {
  const btn = document.getElementById('btn-run-strategy');
  const status = document.getElementById('run-status');
  btn.disabled = true; status.textContent = '執行中(Running)...';
  try {
    const weights = {};
    for (const name of Object.keys(config.default_weights)) {
      weights[name] = parseInt(document.getElementById('w-'+name).value) / 100;
    }
    const strategyParams = {};
    for (const [name, s] of Object.entries(config.strategies)) {
      const p = {};
      for (const pn of Object.keys(s.params)) {
        const el = document.getElementById('sp-'+name+'-'+pn);
        const v = el.value;
        p[pn] = isNaN(Number(v)) ? v : Number(v);
      }
      strategyParams[name] = p;
    }
    const body = {
      weights,
      strategy_params: strategyParams,
      top_n_stocks: parseInt(document.getElementById('uf-top-stocks').value),
      top_n_etfs: parseInt(document.getElementById('uf-top-etfs').value),
      as_of_date: document.getElementById('uf-as-of').value || null,
    };
    const res = await fetch('/api/v1/strategies/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    if (!res.ok) { status.textContent = '❌ 失敗(Failed)'; return; }
    const data = await res.json();
    latestResult = data;
    status.innerHTML = '✅ 完成(Done) — 共(Total) ' + data.total_candidates + ' 檔候選(Candidates)';
    renderResult(data);
    // switch to result
    document.getElementById('result-section').style.display = 'block';
    document.getElementById('result-section').scrollIntoView({behavior:'smooth'});
  } catch(e) { status.textContent = '❌ '+e.message; }
  finally { btn.disabled = false; }
}

function renderResult(data) {
  const all = [...(data.stocks||[]), ...(data.etfs||[])];
  if (!all.length) { document.getElementById('result-body').innerHTML = '<p class="loading">無結果(No results)</p>'; return; }
  let html = `<p>評分日期(Score Date): ${data.date} · 總候選(Total Candidates): ${data.total_candidates}</p>`;
  html += `<table><thead><tr><th>股號(ID)</th><th>分數(Score)</th><th>排名(Rank)</th></tr></thead><tbody>`;
  for (const item of all) {
    html += `<tr onclick="openStock('${item.stock_id}')"><td class="clickable">${item.stock_id}</td><td>${item.score}</td><td>#${item.rank}</td></tr>`;
  }
  html += `</tbody></table>`;
  document.getElementById('result-body').innerHTML = html;
}

// ── Backtest ──
async function loadBacktest() {
  document.getElementById('bt-start').value = '2024-01-01';
  document.getElementById('bt-end').value = new Date().toISOString().slice(0,10);
  const rows = await fetch('/api/v1/backtest/history').then(r=>r.json()).catch(()=>[]);
  const tb = document.getElementById('bt-history-body');
  if (!rows.length) { tb.innerHTML = '<tr><td colspan="6" class="loading">尚無回測紀錄(No backtest history)</td></tr>'; return; }
  for (const r of rows) {
    tb.innerHTML += `<tr onclick="openStock('${r.run_id}')"><td style="font-family:monospace;font-size:.75rem">${(r.run_id||'').slice(0,8)}</td>
      <td>${r.start_date||''}→${r.end_date||''}</td>
      <td>${r.total_return!=null?(r.total_return*100).toFixed(2)+'%':'-'}</td>
      <td>${r.cagr!=null?(r.cagr*100).toFixed(2)+'%':'-'}</td>
      <td>${r.sharpe!=null?r.sharpe.toFixed(2):'-'}</td>
      <td>${r.max_drawdown!=null?r.max_drawdown.toFixed(2)+'%':'-'}</td></tr>`;
  }
}

async function runBacktest() {
  const btn = document.querySelector('#tab-backtest .btn-warning');
  const status = document.getElementById('bt-status');
  btn.disabled = true; status.textContent = '執行回測中(Backtesting)...';
  try {
    const weights = {};
    for (const name of Object.keys(config.default_weights || DEFAULT_WEIGHTS)) {
      const el = document.getElementById('w-'+name);
      weights[name] = el ? parseInt(el.value)/100 : (config.default_weights||{})[name];
    }
    const res = await fetch('/api/v1/backtest/run', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        start_date: document.getElementById('bt-start').value,
        end_date: document.getElementById('bt-end').value || null,
        strategy_weights: weights,
      }),
    });
    if (!res.ok) { status.textContent = '❌ 回測失敗(Backtest Failed)'; return; }
    const data = await res.json();
    status.innerHTML = '✅ 回測完成(Done) — ID: ' + data.run_id.slice(0,8) + '...';
    loadBacktest();
  } catch(e) { status.textContent = '❌ '+e.message; }
  finally { btn.disabled = false; }
}

// ── Init ──
loadDashboard();
loadStrategyConfig().then(() => { /* wait */ });
loadBacktest();
</script>
</body>
</html>"""
